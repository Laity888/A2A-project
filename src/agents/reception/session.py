"""会话上下文存储（工单 05，票 06 §9；工单：持久化记忆 · 会话短期历史落 MySQL）。

接待是唯一有状态者：按 session_key（service 组合的 `{user_id}:{context_id}`）存最近 N 轮。
后端由 session.backend 选择：
- memory（默认）：进程内 deque，离线可用、零依赖，但重启即丢。
- mysql：落 VM MySQL `mem_session` 表，接待进程重启/同浏览器同会话可续上（跨设备仍需同一 context_id）。
  读写全部 try/except 降级：读失败当无历史、写失败静默，绝不因 DB 抖动打断主链路。
- redis：留部署阶段（需 redis 包，当前 VM 未必起）。

MySQLSessionStore 接受注入的 connection_factory → 离线用假 DB-API 连接测编排，不碰真库。
"""

from __future__ import annotations

import logging
import os
import json
import re
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Callable

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _now_str() -> str:
    """本地可读时间（'YYYY-MM-DD HH:MM:SS'）——写库用，取代 epoch 数字。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _table_ident(name: str) -> str:
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"非法表名：{name!r}（仅允许字母数字下划线）")
    return name


class InMemorySessionStore:
    def __init__(self, max_rounds: int = 6) -> None:
        self._max_rounds = int(max_rounds)
        self._store: dict[str, deque] = defaultdict(lambda: deque(maxlen=self._max_rounds * 2))

    async def history(self, context_id: str) -> list[dict[str, str]]:
        return list(self._store[context_id])

    async def append(self, context_id: str, user_text: str, assistant_text: str, trace: dict | None = None) -> None:
        buf = self._store[context_id]
        buf.append({"role": "user", "text": user_text})
        turn = {"role": "assistant", "text": assistant_text}
        if trace is not None:
            turn["trace"] = trace
        buf.append(turn)


class RedisSessionStore:
    """Redis 版（key=prefix+context_id，存最近 N 轮，TTL 可配）。延迟到部署阶段接入。"""

    def __init__(self, *, host: str, port: int, key_prefix: str,
                 max_rounds: int = 6, ttl_seconds: int = 3600) -> None:
        import json  # noqa: F401  (占位，序列化用)
        import redis

        self._json = json
        self._r = redis.Redis(host=host, port=port, decode_responses=True)
        self._prefix = key_prefix
        self._max_items = max_rounds * 2
        self._ttl = ttl_seconds

    async def history(self, context_id: str) -> list[dict[str, str]]:
        raw = self._r.lrange(self._prefix + context_id, 0, -1)
        return [self._json.loads(x) for x in raw]

    async def append(self, context_id: str, user_text: str, assistant_text: str, trace: dict | None = None) -> None:
        key = self._prefix + context_id
        assistant = {"role": "assistant", "text": assistant_text}
        if trace is not None:
            assistant["trace"] = trace
        for turn in ({"role": "user", "text": user_text}, assistant):
            self._r.rpush(key, self._json.dumps(turn, ensure_ascii=False))
        self._r.ltrim(key, -self._max_items, -1)
        self._r.expire(key, self._ttl)


class MySQLSessionStore:
    """会话短期历史落 MySQL `mem_session`（工单：持久化记忆）。接口同 InMemorySessionStore。

    - connection_factory() 返回 DB-API 连接（autocommit、DictCursor）；离线测注入假连接。
    - 首次用时建表（CREATE TABLE IF NOT EXISTS），之后 history/append 全 try/except 降级。
    """

    def __init__(self, *, connection_factory: Callable[[], Any], max_rounds: int = 6,
                 table: str = "mem_session") -> None:
        self._cf = connection_factory
        self._max_items = int(max_rounds) * 2
        self._table = _table_ident(table)
        self._ensured = False

    def _ensure_table(self) -> None:
        if self._ensured:
            return
        conn = self._cf()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        id         BIGINT AUTO_INCREMENT PRIMARY KEY,
                        session_key VARCHAR(160) NOT NULL,
                        role       VARCHAR(16) NOT NULL,
                        text       MEDIUMTEXT NOT NULL,
                        trace      MEDIUMTEXT NULL,
                        created_at DATETIME NOT NULL,
                        INDEX idx_key (session_key, id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                # 老表（早期无 trace 列）幂等补列
                cur.execute(f"SHOW COLUMNS FROM {self._table} LIKE 'trace'")
                if not cur.fetchall():
                    cur.execute(f"ALTER TABLE {self._table} ADD COLUMN trace MEDIUMTEXT NULL")
                self._migrate_legacy_created_at(cur)
        finally:
            conn.close()
        self._ensured = True

    def _migrate_legacy_created_at(self, cur) -> None:
        """把历史 epoch(DOUBLE) 的 created_at 迁移成真实 DATETIME（幂等，仅当列仍是数值类型才动）。"""
        t = self._table
        try:
            cur.execute(
                "SELECT DATA_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = 'created_at'",
                (t,),
            )
            rows = cur.fetchall() or []
            row = rows[0] if rows else None
            if isinstance(row, dict):
                dtype = row.get("DATA_TYPE")
            elif isinstance(row, (list, tuple)) and row:
                dtype = row[0]
            else:
                dtype = None
            if dtype not in ("double", "float", "decimal", "real"):
                return
            cur.execute(f"ALTER TABLE {t} ADD COLUMN created_at_new DATETIME NULL")
            cur.execute(f"UPDATE {t} SET created_at_new = FROM_UNIXTIME(created_at)")
            cur.execute(f"ALTER TABLE {t} DROP COLUMN created_at")
            cur.execute(f"ALTER TABLE {t} CHANGE COLUMN created_at_new created_at DATETIME NOT NULL")
            logger.info("mem_session.created_at 已从 epoch 迁移为 DATETIME：%s", t)
        except Exception as exc:  # noqa: BLE001 - 迁移失败不阻断，新表本就是 DATETIME
            logger.warning("mem_session.created_at 迁移跳过：%s", exc)

    async def history(self, context_id: str) -> list[dict[str, str]]:
        try:
            self._ensure_table()
            conn = self._cf()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT role, text FROM {self._table} "
                        "WHERE session_key=%s ORDER BY id DESC LIMIT %s",
                        (context_id, self._max_items),
                    )
                    rows = cur.fetchall()
            finally:
                conn.close()
            return [{"role": r["role"], "text": r["text"]} for r in reversed(list(rows))]
        except Exception as exc:  # noqa: BLE001 - 读失败当无历史，主链路继续
            logger.warning("会话历史读取失败（按空历史处理）key=%s err=%s", context_id, exc)
            return []

    async def append(self, context_id: str, user_text: str, assistant_text: str, trace: dict | None = None) -> None:
        try:
            self._ensure_table()
            now = _now_str()
            trace_json = json.dumps(trace, ensure_ascii=False) if trace is not None else None
            conn = self._cf()
            try:
                with conn.cursor() as cur:
                    for role, text, tr in (("user", user_text, None), ("assistant", assistant_text, trace_json)):
                        cur.execute(
                            f"INSERT INTO {self._table}(session_key, role, text, trace, created_at) "
                            "VALUES(%s,%s,%s,%s,%s)",
                            (context_id, role, text, tr, now),
                        )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - 写失败静默，不影响本次答复
            logger.warning("会话历史写入失败（忽略）key=%s err=%s", context_id, exc)


def _make_mysql_connection_factory(mcfg: dict[str, Any]):
    """按 config 的 mysql 段：先建库（不含 db 连接），返回带 db 的连接工厂。"""
    import pymysql
    from dotenv import load_dotenv
    from pymysql.cursors import DictCursor

    from common.config import DEFAULT_ENV_PATH
    load_dotenv(DEFAULT_ENV_PATH, override=False)

    host = mcfg.get("host", "127.0.0.1")
    port = int(mcfg.get("port", 3306))
    user = mcfg.get("user", "root")
    password = os.getenv(mcfg.get("password_env", "A2A_MYSQL_PASSWORD"), "").strip()
    database = mcfg.get("database", "a2a_cs")
    if not _IDENT_RE.match(database or ""):
        raise ValueError(f"非法库名：{database!r}")

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

    return _factory


# 公开别名：memory store / UI profile store 复用同一套建库+连接工厂，避免重复实现。
make_mysql_connection_factory = _make_mysql_connection_factory


def make_session_store(cfg) -> Any:
    s = cfg.settings.get("session", {})
    backend = s.get("backend", "memory")
    max_rounds = int(s.get("max_history_rounds", 6))
    if backend == "redis":
        m = cfg.common.infra.redis
        return RedisSessionStore(
            host=m.host, port=m.port, key_prefix=s.get("redis_key_prefix", "a2a_cs:ctx:"),
            max_rounds=max_rounds, ttl_seconds=int(s.get("ttl_seconds", 3600)),
        )
    if backend == "mysql":
        mcfg = s.get("mysql", {}) or {}
        try:
            factory = _make_mysql_connection_factory(mcfg)
        except Exception as exc:  # noqa: BLE001 - 建库/连库失败 → 退回内存，接待不因 DB 起不来
            logger.warning("MySQL 会话存储初始化失败，退回内存 backend：%s", exc)
            return InMemorySessionStore(max_rounds=max_rounds)
        return MySQLSessionStore(
            connection_factory=factory, max_rounds=max_rounds, table=mcfg.get("table", "mem_session")
        )
    return InMemorySessionStore(max_rounds=max_rounds)
