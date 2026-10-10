"""接待层答案缓存（工单：重复问题省算力；工单：多用户隔离按 user_id 作用域）。

进程内、按「(作用域 + 归一化后的问题文本)」缓存最终答复，命中即直接返回、跳过
triage→ReAct→检索→生成整条链路。接待单 worker（uvicorn_workers=1）内存缓存即全局有效。

作用域（scope）：
- "user"（默认，多用户隔离）：key 带 user_id 前缀，A 用户的答案不会命中给 B 用户，
  user_id 为空时落到 "anon" 桶（离线测试/未登录仍可用）。
- "global"：不带 user_id，跨用户共享（命中率最高，但不做隔离）。

要点：
- key 归一化：去空白、转小写、去常见中英文标点，让"支持哪些文件格式"与"支持的文件格式有哪些"归一。
- TTL：过期自动失效（默认一天），避免知识库更新后答旧的。
- LRU 上限：超容量按最久未用淘汰，防无界增长。
- 时钟可注入（time_fn），便于离线测 TTL 过期不真等待。
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from typing import Callable

# 归一化时剔除的标点（中英文常见），折叠空白
_PUNCT_RE = re.compile(r"[，。、？！：；．,\.!\?:;\"'“”‘’()（）\[\]【】{}<>《》~`|/\\—_\-·]+")
_WS_RE = re.compile(r"\s+")
_SEP = "\x1f"  # 作用域前缀与归一化文本之间的分隔符
_ANON = "anon"


class AnswerCache:
    def __init__(
        self, *, enabled: bool = True, ttl_seconds: float = 86400.0,
        normalize: bool = True, max_entries: int = 512, scope: str = "user",
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._ttl = float(ttl_seconds)
        self._normalize = bool(normalize)
        self._max = int(max_entries)
        self._scope = scope if scope in ("user", "global") else "user"
        self._now = time_fn or time.time
        self._store: "OrderedDict[str, tuple[float, str]]" = OrderedDict()  # key -> (expire_ts, answer)

    def _key(self, text: str, user_id: str | None = None, pref_sig: str = "") -> str:
        t = (text or "").strip()
        if self._normalize:
            t = _WS_RE.sub("", t.lower())
            t = _PUNCT_RE.sub("", t)
        if not t:
            return ""
        sig = pref_sig or ""
        if self._scope == "user":
            return (user_id or _ANON) + _SEP + sig + _SEP + t
        return sig + _SEP + t

    def get(self, text: str, user_id: str | None = None, pref_sig: str = "") -> str | None:
        if not self._enabled:
            return None
        key = self._key(text, user_id, pref_sig)
        if not key:
            return None
        item = self._store.get(key)
        if item is None:
            return None
        expire, answer = item
        if self._now() >= expire:            # 过期即删
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)         # LRU：命中置最近
        return answer

    def set(self, text: str, answer: str, user_id: str | None = None, pref_sig: str = "") -> None:
        if not self._enabled or not answer:
            return
        key = self._key(text, user_id, pref_sig)
        if not key:
            return
        self._store[key] = (self._now() + self._ttl, answer)
        self._store.move_to_end(key)
        while len(self._store) > self._max:  # 超容量淘汰最久未用
            self._store.popitem(last=False)

    def __len__(self) -> int:  # 便于测试/观测
        return len(self._store)
