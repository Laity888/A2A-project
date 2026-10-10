"""长期记忆 & 用户偏好（工单：长期记忆 + 用户偏好，轻量结构化版）。

与语义缓存(cs_qc_lzs)划清界限：语义缓存记的是「问题→答案」复用加速；这里记的是
「关于这个用户」——① 永久偏好(KV) ② 跨会话的意图/问题滚动流水(长期记忆)。

存储全在 VM MySQL `a2a_cs`（和 users/mem_session/conversations 同库）：
- user_profiles(user_id, profile_key, profile_value, updated_at)  主键(user_id, profile_key)
- query_history(id, user_id, intent_type, query_content JSON, query_time)  索引(user_id, query_time DESC)

设计取向（贴合本项目多 Agent / 按请求 user_id 的形态）：
- 无状态：不照搬"每用户常驻实例 + 内存镜像"，方法一律按 user_id 传参、每次直查 DB。
  接待是 uvicorn_workers=1，读一次 profile/history 成本极低（都是走索引的小查询）。
- 全程 try/except 降级：读失败当「无偏好 / 无历史」，写失败静默，绝不因 DB 抖动打断主链路
  （与 session.py / semantic_cache.py 同一铁律）。
- 多用户隔离：user_id 贯穿所有 SQL；user_id 为空落 "anon" 桶（离线/未登录仍可用）。
- 容量控制：偏好无上限（本就小、全量注入）；历史读取时 LIMIT 控制，表里不删旧行（留审计，不加 TTL）。
- 边界：query_history 只存用户侧意图 + 归一化问题，绝不重复存答案正文——逐轮原文仍归 mem_session。

connection_factory 可注入 → 离线用假 DB-API 测编排，不碰真库。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ANON = "anon"
_PROFILE_KEY_MAX = 100
_PROFILE_VALUE_MAX = 500


def _table_ident(name: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"非法表名：{name!r}（仅允许字母数字下划线）")
    return name


def _clean_profile_updates(updates: dict) -> dict[str, str]:
    """归一化偏好写入：去空白、丢空项、拒非法 key（超长/控制字符）、拒含控制字符的 value、截断 value。"""
    clean: dict[str, str] = {}
    for k, v in (updates or {}).items():
        k = str(k).strip()
        v = str(v).strip()
        if not k or not v:
            continue
        if len(k) > _PROFILE_KEY_MAX or _CONTROL_RE.search(k):
            logger.warning("忽略非法偏好 key（超长或含控制字符）key=%r", k[:40])
            continue
        if _CONTROL_RE.search(v):
            continue
        clean[k] = v[:_PROFILE_VALUE_MAX]
    return clean


def _fmt_time(qt: Any) -> str:
    """把 DB 的 query_time 归一化成字符串：datetime→strftime，其它→str，None→空。"""
    if qt is None:
        return ""
    if hasattr(qt, "strftime"):
        return qt.strftime("%Y-%m-%d %H:%M:%S")
    return str(qt)


class MemoryStore:
    """用户偏好(KV) + 跨会话意图/问题流水(长期记忆)，落 MySQL。"""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        profile_table: str = "user_profiles",
        history_table: str = "query_history",
        history_limit: int = 20,
        inject_recent: int = 5,
        ensure_schema: bool = False,
    ) -> None:
        self._cf = connection_factory
        self._profile_table = _table_ident(profile_table)
        self._history_table = _table_ident(history_table)
        self._history_limit = int(history_limit)
        self._inject_recent = int(inject_recent)
        self._ensured = False
        if ensure_schema:
            self._ensure_schema()

    # ---------------- 建表（幂等，首次用时）----------------
    def _ensure_schema(self) -> None:
        if self._ensured:
            return
        conn = self._cf()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._profile_table} (
                        user_id       VARCHAR(64)  NOT NULL,
                        profile_key   VARCHAR(100) NOT NULL,
                        profile_value VARCHAR(500) NOT NULL,
                        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, profile_key)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._history_table} (
                        id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                        user_id       VARCHAR(64)  NOT NULL,
                        intent_type   VARCHAR(100) NOT NULL,
                        query_content JSON         NOT NULL,
                        query_time    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (id),
                        INDEX idx_user_time (user_id, query_time DESC)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
        finally:
            conn.close()
        self._ensured = True

    @staticmethod
    def _uid(user_id: str | None) -> str:
        return user_id or _ANON

    # ---------------- 偏好 ----------------
    def get_profile(self, user_id: str | None) -> dict[str, str]:
        try:
            self._ensure_schema()
            conn = self._cf()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT profile_key, profile_value FROM {self._profile_table} WHERE user_id=%s",
                        (self._uid(user_id),),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
            return {r["profile_key"]: r["profile_value"] for r in rows}
        except Exception as exc:  # noqa: BLE001 - 读失败当无偏好
            logger.warning("偏好读取失败（当无偏好）user_id=%s err=%s", user_id, exc)
            return {}

    def get_profile_text(self, user_id: str | None) -> str:
        prof = self.get_profile(user_id)
        if not prof:
            return ""
        return "，".join(f"{k}: {v}" for k, v in prof.items())

    def update_profile(self, user_id: str | None, updates: dict[str, str]) -> bool:
        updates = _clean_profile_updates(updates)
        if not updates:
            return False
        try:
            self._ensure_schema()
            uid = self._uid(user_id)
            conn = self._cf()
            try:
                with conn.cursor() as cur:
                    for k, v in updates.items():
                        cur.execute(
                            f"INSERT INTO {self._profile_table}(user_id, profile_key, profile_value) "
                            "VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE profile_value=%s",
                            (uid, k, v, v),
                        )
            finally:
                conn.close()
            return True
        except Exception as exc:  # noqa: BLE001 - 写失败静默
            logger.warning("偏好写入失败（忽略）user_id=%s err=%s", user_id, exc)
            return False

    def delete_profile(self, user_id: str | None, keys: list[str]) -> bool:
        keys = [str(k).strip() for k in (keys or []) if str(k).strip()]
        if not keys:
            return False
        try:
            self._ensure_schema()
            uid = self._uid(user_id)
            conn = self._cf()
            try:
                with conn.cursor() as cur:
                    ph = ",".join(["%s"] * len(keys))
                    cur.execute(
                        f"DELETE FROM {self._profile_table} WHERE user_id=%s AND profile_key IN ({ph})",
                        (uid, *keys),
                    )
            finally:
                conn.close()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("偏好删除失败（忽略）user_id=%s err=%s", user_id, exc)
            return False

    # ---------------- 历史（长期记忆）----------------
    def append_history(self, user_id: str | None, intent: str, query: str, context_id: str | None = None) -> None:
        q = (query or "").strip()
        if not q:
            return
        try:
            self._ensure_schema()
            conn = self._cf()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"INSERT INTO {self._history_table}(user_id, intent_type, query_content) "
                        "VALUES(%s,%s,%s)",
                        (
                            self._uid(user_id),
                            (intent or "")[:100],
                            json.dumps({"query": q[:500], "context_id": context_id}, ensure_ascii=False),
                        ),
                    )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - 写失败静默
            logger.warning("历史写入失败（忽略）user_id=%s err=%s", user_id, exc)

    def get_recent_history(self, user_id: str | None, n: int | None = None) -> list[dict[str, Any]]:
        limit = int(n) if n else self._inject_recent
        try:
            self._ensure_schema()
            conn = self._cf()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT intent_type, query_content, query_time FROM {self._history_table} "
                        "WHERE user_id=%s ORDER BY id DESC LIMIT %s",
                        (self._uid(user_id), limit),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
            out: list[dict[str, Any]] = []
            for r in reversed(list(rows)):
                try:
                    content = json.loads(r["query_content"]) if isinstance(r["query_content"], str) else r["query_content"]
                except (ValueError, TypeError):
                    content = {}
                out.append({
                    "intent": r["intent_type"],
                    "query": (content or {}).get("query", ""),
                    "time": _fmt_time(r.get("query_time")),
                })
            return out
        except Exception as exc:  # noqa: BLE001 - 读失败当无历史
            logger.warning("历史读取失败（当空）user_id=%s err=%s", user_id, exc)
            return []

    # ---------------- 组合：给答复注入的记忆上下文 ----------------
    def load_context(self, user_id: str | None) -> dict[str, Any]:
        """返回 {"profile_text": str, "recent": [问题短句,...]}；两者皆空时上层不注入。"""
        prof = self.get_profile(user_id)
        profile_text = "，".join(f"{k}: {v}" for k, v in prof.items())
        recent = [h["query"] for h in self.get_recent_history(user_id) if h.get("query")]
        return {"profile_text": profile_text, "recent": recent}

    # ---------------- 清空（用户主动）----------------
    def clear_history(self, user_id: str | None) -> None:
        try:
            self._ensure_schema()
            conn = self._cf()
            try:
                with conn.cursor() as cur:
                    cur.execute(f"DELETE FROM {self._history_table} WHERE user_id=%s", (self._uid(user_id),))
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("清空历史失败（忽略）user_id=%s err=%s", user_id, exc)

    def clear_profile(self, user_id: str | None) -> None:
        try:
            self._ensure_schema()
            conn = self._cf()
            try:
                with conn.cursor() as cur:
                    cur.execute(f"DELETE FROM {self._profile_table} WHERE user_id=%s", (self._uid(user_id),))
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("清空偏好失败（忽略）user_id=%s err=%s", user_id, exc)

    def clear(self, user_id: str | None) -> None:
        self.clear_profile(user_id)
        self.clear_history(user_id)


def make_memory_store(cfg) -> MemoryStore | None:
    """按 cfg.settings['memory'] 装配；enabled=false 或 mysql 初始化失败 → None（接待不因它起不来）。"""
    m = cfg.settings.get("memory", {}) or {}
    if not m.get("enabled"):
        return None
    from agents.reception.session import make_mysql_connection_factory

    try:
        factory = make_mysql_connection_factory(m.get("mysql", {}) or {})
    except Exception as exc:  # noqa: BLE001 - 连不上库直接关闭记忆功能
        logger.warning("记忆存储初始化失败，长期记忆/偏好功能关闭：%s", exc)
        return None
    return MemoryStore(
        connection_factory=factory,
        profile_table=m.get("profile_table", "user_profiles"),
        history_table=m.get("history_table", "query_history"),
        history_limit=int(m.get("history_limit", 20)),
        inject_recent=int(m.get("inject_recent", 5)),
    )
