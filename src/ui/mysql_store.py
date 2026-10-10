"""MySQL 版用户存储（工单：登录迁 VM MySQL）。

与 SQLite UserStore 同接口（create_user / verify / username），复用 auth.hash_password 的
PBKDF2 散列；登录 cookie 仍由 auth 的 HMAC token 负责，这里只管账号表。

- 建库/建表/种子在装配时幂等完成（CREATE DATABASE/TABLE IF NOT EXISTS + 灌内置账号）。
- 口令只从环境变量读（password_env），不落配置文件。
- connection_factory 可注入 → 离线用假连接测通编排，不碰真 MySQL。
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import uuid
from typing import Any, Callable, Iterable

from ui.auth import _now_str, hash_password, validate_new_user

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _ident(name: str, kind: str) -> str:
    """库名/表名白名单校验（不能参数化，只能拼进 SQL，故严格过滤）。"""
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"非法{kind}名：{name!r}（仅允许字母数字下划线）")
    return name


class MySQLUserStore:
    def __init__(self, *, connection_factory: Callable[[], Any], table: str = "users",
                 ensure_schema: bool = True) -> None:
        self._cf = connection_factory
        self._table = _ident(table, "表")
        if ensure_schema:
            self._ensure_schema()

    def _conn(self):
        return self._cf()

    def _ensure_schema(self) -> None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        user_id    VARCHAR(64) PRIMARY KEY,
                        username   VARCHAR(64) NOT NULL UNIQUE,
                        pw_hash    CHAR(64) NOT NULL,
                        salt       CHAR(32) NOT NULL,
                        created_at DATETIME NOT NULL
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                self._migrate_legacy_created_at(cur)
        finally:
            conn.close()

    def _migrate_legacy_created_at(self, cur) -> None:
        """把历史 epoch(DOUBLE) 列迁移成真实 DATETIME（幂等，仅当列仍是数值类型才动）。"""
        try:
            cur.execute(
                "SELECT DATA_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = 'created_at'",
                (self._table,),
            )
            row = cur.fetchone()
            if isinstance(row, dict):
                dtype = row.get("DATA_TYPE")
            elif isinstance(row, (list, tuple)) and row:
                dtype = row[0]
            else:
                dtype = None
            if dtype not in ("double", "float", "decimal", "real"):
                return
            t = self._table
            cur.execute(f"ALTER TABLE {t} ADD COLUMN created_at_new DATETIME NULL")
            cur.execute(f"UPDATE {t} SET created_at_new = FROM_UNIXTIME(created_at)")
            cur.execute(f"ALTER TABLE {t} DROP COLUMN created_at")
            cur.execute(f"ALTER TABLE {t} CHANGE COLUMN created_at_new created_at DATETIME NOT NULL")
            logger.info("users.created_at 已从 epoch 迁移为 DATETIME：%s", t)
        except Exception as exc:  # noqa: BLE001 - 迁移失败不阻断（离线假游标/权限受限等），新库本就是 DATETIME
            logger.warning("users.created_at 迁移跳过：%s", exc)

    def exists(self, username: str) -> bool:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT 1 AS x FROM {self._table} WHERE username=%s", ((username or "").strip(),))
                return cur.fetchone() is not None
        finally:
            conn.close()

    def create_user(self, username: str, password: str) -> tuple[bool, str]:
        """成功 (True, user_id)；失败 (False, 中文原因)。"""
        err = validate_new_user(username, password)
        if err:
            return False, err
        name = username.strip()
        if self.exists(name):
            return False, "用户名已存在"
        salt = secrets.token_hex(16)
        uid = uuid.uuid4().hex
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self._table}(user_id, username, pw_hash, salt, created_at) "
                    "VALUES(%s,%s,%s,%s,%s)",
                    (uid, name, hash_password(password, salt), salt, _now_str()),
                )
        finally:
            conn.close()
        return True, uid

    def verify(self, username: str, password: str) -> str | None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT user_id, pw_hash, salt FROM {self._table} WHERE username=%s",
                    ((username or "").strip(),),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            hash_password(password or "", secrets.token_hex(16))  # 拉近耗时，抗用户名枚举
            return None
        if row["pw_hash"] != hash_password(password or "", row["salt"]):
            return None
        return row["user_id"]

    def username(self, user_id: str) -> str | None:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT username FROM {self._table} WHERE user_id=%s", (user_id,))
                row = cur.fetchone()
        finally:
            conn.close()
        return row["username"] if row else None

    def seed(self, seed_users: Iterable[Any]) -> None:
        """内置账号（存在则跳过）。条目支持 dict{username,password} 或 (username, password)。"""
        for item in seed_users or []:
            if isinstance(item, dict):
                name, password = item.get("username"), item.get("password")
            else:
                name, password = item[0], item[1]
            if not name or not password:
                continue
            if not self.exists(name):
                ok, result = self.create_user(name, password)
                logger.info("种子用户 %s：%s", name, "已建" if ok else f"跳过({result})")


def make_mysql_store(mysql_cfg: dict[str, Any], *, seed_users: Iterable[Any] = ()) -> MySQLUserStore:
    """按 config.auth.mysql 段建库建表 + 灌种子，返回可注入生产 AuthContext 的 store。"""
    import pymysql
    from dotenv import load_dotenv
    from pymysql.cursors import DictCursor

    from common.config import DEFAULT_ENV_PATH
    load_dotenv(DEFAULT_ENV_PATH, override=False)

    host = mysql_cfg.get("host", "127.0.0.1")
    port = int(mysql_cfg.get("port", 3306))
    user = mysql_cfg.get("user", "root")
    password = os.getenv(mysql_cfg.get("password_env", "A2A_MYSQL_PASSWORD"), "").strip()
    database = _ident(mysql_cfg.get("database", "a2a_cs"), "库")
    table = mysql_cfg.get("table", "users")

    def _connect(with_db: bool):
        kwargs: dict[str, Any] = dict(
            host=host, port=port, user=user, password=password,
            autocommit=True, cursorclass=DictCursor, charset="utf8mb4",
        )
        if with_db:
            kwargs["database"] = database
        return pymysql.connect(**kwargs)

    # 建库（不含 database 连接）
    boot = _connect(False)
    try:
        with boot.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {database} DEFAULT CHARACTER SET utf8mb4")
    finally:
        boot.close()

    store = MySQLUserStore(connection_factory=lambda: _connect(True), table=table)
    store.seed(seed_users)
    return store
