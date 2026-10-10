"""会话索引存储（工单：多会话历史列表）。落 VM MySQL `conversations` 表。

职责边界：接待仍负责每会话消息正文的写入（mem_session）；本模块只管“某用户有哪些会话、
标题、排序”，并在切换/删除时读/清 mem_session（同库不同表，务实取舍）。

- 接口可注入 connection_factory → 离线用假 DB-API 测编排，不碰真库。
- 库名/表名白名单校验；口令只从环境变量读（复用 auth.mysql 那套）。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Callable

from ui.auth import _now_str

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_TITLE_MAX = 40


def _parse_trace(raw: Any) -> dict | None:
    """mem_session.trace 可能是 JSON 字符串 / 已是 dict / None；坏数据当 None。"""
    if raw is None or isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            val = json.loads(raw)
            return val if isinstance(val, dict) else None
        except (ValueError, TypeError):
            return None
    return None


def _ident(name: str, kind: str = "表") -> str:
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"非法{kind}名：{name!r}（仅允许字母数字下划线）")
    return name


def derive_title(text: str) -> str:
    """首条提问 → 会话标题：折叠空白、截断，空则“新会话”。"""
    t = " ".join((text or "").split()).strip()
    if not t:
        return "新会话"
    return t[:_TITLE_MAX] + "…" if len(t) > _TITLE_MAX else t


def _to_epoch(v: Any) -> Any:
    """把 DATETIME（或历史 epoch / 字符串）统一成秒数，喂给现有前端 fmtWhen；无法解析返回原值。"""
    if isinstance(v, datetime):
        return v.timestamp()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).timestamp()
            except ValueError:
                continue
        try:
            return float(s)
        except ValueError:
            return v
    return v


class ConversationStore:
    def __init__(self, *, connection_factory: Callable[[], Any],
                 table: str = "conversations", message_table: str = "mem_session",
                 max_list: int = 100, message_limit: int = 200, ensure_schema: bool = True) -> None:
        self._cf = connection_factory
        self._table = _ident(table, "表")
        self._msg_table = _ident(message_table, "表")
        self._max_list = int(max_list)
        self._msg_limit = int(message_limit)
        self._ensured = False
        if ensure_schema:
            self.ensure_schema()

    def _conn(self):
        return self._cf()

    def ensure_schema(self) -> None:
        if self._ensured:
            return
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        user_id    VARCHAR(64) NOT NULL,
                        context_id VARCHAR(80) NOT NULL,
                        title      VARCHAR(200) NOT NULL DEFAULT '新会话',
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL,
                        PRIMARY KEY (user_id, context_id),
                        INDEX idx_user_updated (user_id, updated_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                self._migrate_legacy_times(cur)
        finally:
            conn.close()
        self._ensured = True

    def _migrate_legacy_times(self, cur) -> None:
        """把历史 epoch(DOUBLE) 的两个时间列迁移成真实 DATETIME（幂等，仅当列仍是数值类型才动）。"""
        t = self._table
        try:
            cur.execute(
                "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                "AND COLUMN_NAME IN ('created_at', 'updated_at')",
                (t,),
            )
            dtmap: dict[str, str] = {}
            for r in (cur.fetchall() or []):
                if isinstance(r, dict):
                    dtmap[r.get("COLUMN_NAME")] = r.get("DATA_TYPE")
            numeric = ("double", "float", "decimal", "real")
            if not any(dtmap.get(c) in numeric for c in ("created_at", "updated_at")):
                return
            cur.execute(f"ALTER TABLE {t} DROP INDEX idx_user_updated")
            cur.execute(f"ALTER TABLE {t} ADD COLUMN created_at_new DATETIME NULL")
            cur.execute(f"ALTER TABLE {t} ADD COLUMN updated_at_new DATETIME NULL")
            cur.execute(
                f"UPDATE {t} SET created_at_new = FROM_UNIXTIME(created_at), "
                "updated_at_new = FROM_UNIXTIME(updated_at)"
            )
            cur.execute(f"ALTER TABLE {t} DROP COLUMN created_at")
            cur.execute(f"ALTER TABLE {t} DROP COLUMN updated_at")
            cur.execute(f"ALTER TABLE {t} CHANGE COLUMN created_at_new created_at DATETIME NOT NULL")
            cur.execute(f"ALTER TABLE {t} CHANGE COLUMN updated_at_new updated_at DATETIME NOT NULL")
            cur.execute(f"ALTER TABLE {t} ADD INDEX idx_user_updated (user_id, updated_at)")
            logger.info("conversations 时间列已从 epoch 迁移为 DATETIME：%s", t)
        except Exception as exc:  # noqa: BLE001 - 迁移失败不阻断（离线假游标/权限受限等），新库本就是 DATETIME
            logger.warning("conversations 时间列迁移跳过：%s", exc)

    def list(self, user_id: str) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT context_id, title, created_at, updated_at FROM {self._table} "
                    "WHERE user_id=%s ORDER BY updated_at DESC LIMIT %s",
                    (user_id, self._max_list),
                )
                # DB 存的是真实 DATETIME；对外仍回传秒数，保持前端 fmtWhen 不变
                return [
                    {**dict(r),
                     "created_at": _to_epoch(r.get("created_at")),
                     "updated_at": _to_epoch(r.get("updated_at"))}
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()

    def touch_or_create(self, user_id: str, context_id: str, first_text: str) -> str:
        """新会话→建行(标题取首句)；已存在→只刷新 updated_at（不改标题）。返回标题。"""
        now = _now_str()
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT title FROM {self._table} WHERE user_id=%s AND context_id=%s",
                    (user_id, context_id),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        f"UPDATE {self._table} SET updated_at=%s WHERE user_id=%s AND context_id=%s",
                        (now, user_id, context_id),
                    )
                    return row["title"]
                title = derive_title(first_text)
                cur.execute(
                    f"INSERT INTO {self._table}(user_id, context_id, title, created_at, updated_at) "
                    "VALUES(%s,%s,%s,%s,%s)",
                    (user_id, context_id, title, now, now),
                )
                return title
        finally:
            conn.close()

    def rename(self, user_id: str, context_id: str, title: str) -> bool:
        t = " ".join((title or "").split()).strip()
        if not t:
            return False
        t = t[:200]
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {self._table} SET title=%s WHERE user_id=%s AND context_id=%s",
                    (t, user_id, context_id),
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    def get_messages(self, user_id: str, context_id: str) -> list[dict[str, Any]]:
        """从 mem_session 读该会话历史（session_key={uid}:{cid}），带 trace。
        老表未补 trace 列时回退只读文本；表不存在/其它失败返回 []。"""
        key = f"{user_id}:{context_id}"
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        f"SELECT role, text, trace FROM {self._msg_table} "
                        "WHERE session_key=%s ORDER BY id ASC LIMIT %s",
                        (key, self._msg_limit),
                    )
                    return [{"role": r["role"], "text": r["text"], "trace": _parse_trace(r.get("trace"))}
                            for r in cur.fetchall()]
                except Exception:  # noqa: BLE001 - trace 列可能不存在 → 回退只读文本
                    cur.execute(
                        f"SELECT role, text FROM {self._msg_table} "
                        "WHERE session_key=%s ORDER BY id ASC LIMIT %s",
                        (key, self._msg_limit),
                    )
                    return [{"role": r["role"], "text": r["text"], "trace": None} for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001 - mem_session 可能尚未建，读失败当空
            logger.warning("读会话消息失败（当空）key=%s err=%s", key, exc)
            return []
        finally:
            conn.close()

    def delete(self, user_id: str, context_id: str) -> bool:
        """删会话行 + 清该会话在 mem_session 的消息（同库）。返回是否删到会话。"""
        deleted = False
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {self._table} WHERE user_id=%s AND context_id=%s",
                    (user_id, context_id),
                )
                deleted = cur.rowcount > 0
        finally:
            conn.close()
        try:  # 清消息尽力而为，mem_session 不存在也不报错
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM {self._msg_table} WHERE session_key=%s",
                        (f"{user_id}:{context_id}",),
                    )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("清理会话消息失败（忽略）%s:%s err=%s", user_id, context_id, exc)
        return deleted


def make_conversation_store(ui_cfg: dict[str, Any]) -> ConversationStore:
    """按 ui.yaml 装配真实 MySQL ConversationStore（建库建表）。ui/__main__ 用；测试不自动连库。"""
    import pymysql
    from dotenv import load_dotenv
    from pymysql.cursors import DictCursor

    from common.config import DEFAULT_ENV_PATH
    load_dotenv(DEFAULT_ENV_PATH, override=False)

    m = ui_cfg.get("auth", {}).get("mysql", {}) or {}
    conv = ui_cfg.get("conversations", {}) or {}
    host = m.get("host", "127.0.0.1")
    port = int(m.get("port", 3306))
    user = m.get("user", "root")
    password = os.getenv(m.get("password_env", "A2A_MYSQL_PASSWORD"), "").strip()
    database = _ident(m.get("database", "a2a_cs"), "库")

    boot = pymysql.connect(host=host, port=port, user=user, password=password,
                           autocommit=True, charset="utf8mb4")
    try:
        with boot.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {database} DEFAULT CHARACTER SET utf8mb4")
    finally:
        boot.close()

    def _factory():
        return pymysql.connect(host=host, port=port, user=user, password=password,
                               database=database, autocommit=True,
                               cursorclass=DictCursor, charset="utf8mb4")

    return ConversationStore(
        connection_factory=_factory,
        table=conv.get("table", "conversations"),
        message_table=conv.get("message_table", "mem_session"),
        max_list=int(conv.get("max_list", 100)),
        message_limit=int(conv.get("message_limit", 200)),
    )
