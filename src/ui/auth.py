"""多用户隔离的身份层：极简自包含登录（工单：多用户隔离 → 迁 MySQL）。

用户存储支持两种可互换的 SQL 后端，由 config.auth.backend 选择：
- "sqlite"：项目内 SQLite 文件（默认，零服务、离线可跑，见本模块 UserStore）。
- "mysql"：VM 上的 MySQL users 表（见 ui/mysql_store.py 的 MySQLUserStore）。
两者接口一致（create_user / verify / username），口令统一用 PBKDF2 加盐散列，
登录凭证统一用 HMAC-SHA256 签名 cookie（user_id|exp，无状态、可跨重启校验）。

约定：
- 密钥只从环境变量读（config.secret_env），MySQL 口令也只从环境变量读，均不落配置文件。
- 注册可用 config.auth.registration_enabled 关闭（当前先内置 user1/2/3 演示账号，注册功能后置）。

三块：
- UserStore（SQLite）+ hash_password（共享散列）：MySQLUserStore 复用同一套哈希/token。
- sign_token / verify_token：签名与校验登录 cookie（含过期）。
- AuthContext / make_auth_context：按 config 的 auth 段装配后端 + cookie 名/TTL/密钥/注册开关；
  enabled=False 时整套透明放行（开发/离线测试用）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# 与 hello_world/common 一致：可变项走配置，密钥只从环境变量读。

_MIN_PASSWORD_LEN = 4
_PBKDF2_ROUNDS = 200_000


def hash_password(password: str, salt_hex: str) -> str:
    """PBKDF2-HMAC-SHA256 加盐散列（hex 输出）。SQLite 与 MySQL 后端共用。"""
    dk = hashlib.pbkdf2_hmac(
        "sha256", (password or "").encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS
    )
    return dk.hex()


def validate_new_user(username: str, password: str) -> str | None:
    """校验新用户名/口令；返回中文错误串，合法则 None。两后端共用。"""
    name = (username or "").strip()
    if not name:
        return "用户名不能为空"
    if len(name) > 64:
        return "用户名过长（≤64）"
    if len(password or "") < _MIN_PASSWORD_LEN:
        return f"密码至少 {_MIN_PASSWORD_LEN} 位"
    return None


def _now_str() -> str:
    """本地可读创建时间（'YYYY-MM-DD HH:MM:SS'），与 memory 的 query_time 风格一致。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class UserStore:
    """项目内 SQLite 用户表（user_id / username / 口令散列 / 盐 / 创建时间）。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db = str(db_path)
        Path(self._db).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id    TEXT PRIMARY KEY,
                    username   TEXT UNIQUE NOT NULL,
                    pw_hash    TEXT NOT NULL,
                    salt       TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            # 迁移：把历史上以 Unix epoch 数字存的 created_at 转成本地可读时间（幂等，仅动数字行）。
            try:
                c.execute(
                    "UPDATE users SET created_at = datetime(created_at, 'unixepoch', 'localtime') "
                    "WHERE typeof(created_at) IN ('real', 'integer')"
                )
            except sqlite3.Error:
                pass

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _hash(password: str, salt_hex: str) -> str:
        return hash_password(password, salt_hex)

    def create_user(self, username: str, password: str) -> tuple[bool, str]:
        """成功返回 (True, user_id)；失败返回 (False, 中文原因)。"""
        name = (username or "").strip()
        if not name:
            return False, "用户名不能为空"
        if len(name) > 64:
            return False, "用户名过长（≤64）"
        if len(password or "") < _MIN_PASSWORD_LEN:
            return False, f"密码至少 {_MIN_PASSWORD_LEN} 位"
        salt = secrets.token_hex(16)
        try:
            with self._tx() as c:
                cursor = c.execute(
                    "INSERT INTO users(user_id, username, pw_hash, salt, created_at) VALUES(?,?,?,?,?)",
                    (uuid.uuid4().hex, name, self._hash(password, salt), salt, _now_str()),
                )
                del cursor
        except sqlite3.IntegrityError:
            return False, "用户名已存在"
        with self._tx() as c:
            row = c.execute("SELECT user_id FROM users WHERE username=?", (name,)).fetchone()
        return True, row["user_id"]

    def verify(self, username: str, password: str) -> str | None:
        """校验用户名口令，成功返回 user_id，否则 None。"""
        name = (username or "").strip()
        with self._tx() as c:
            row = c.execute(
                "SELECT user_id, pw_hash, salt FROM users WHERE username=?", (name,)
            ).fetchone()
        if not row:
            # 仍做一次哈希以拉近校验耗时，降低用户名枚举的时序差异
            self._hash(password or "", secrets.token_hex(16))
            return None
        if not hmac.compare_digest(row["pw_hash"], self._hash(password or "", row["salt"])):
            return None
        return row["user_id"]

    def username(self, user_id: str) -> str | None:
        with self._tx() as c:
            row = c.execute("SELECT username FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row["username"] if row else None


def sign_token(user_id: str, secret: str, *, ttl_seconds: float = 604800.0, now: float | None = None) -> str:
    """生成 user_id 的签名 cookie：base64url(user_id|exp).hmac_sha256。"""
    _now = time.time() if now is None else now
    exp = int(_now + float(ttl_seconds))
    body = base64.urlsafe_b64encode(f"{user_id}|{exp}".encode("utf-8")).decode("ascii").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _recover_body(b64: str) -> bytes:
    pad = "=" * (-len(b64) % 4)
    return base64.urlsafe_b64decode(b64 + pad)


def verify_token(token: str | None, secret: str, *, now: float | None = None) -> str | None:
    """校验签名 cookie，有效返回 user_id，否则 None（签名不符/过期/格式坏一律 None）。"""
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expect = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = _recover_body(body).decode("utf-8")
        user_id, exp = payload.rsplit("|", 1)
        exp_ts = float(exp)
    except (ValueError, UnicodeDecodeError):
        return None
    _now = time.time() if now is None else now
    if _now >= exp_ts:
        return None
    return user_id or None


def _resolve_secret(auth_cfg: dict[str, Any], data_dir: Path) -> str:
    """密钥优先取环境变量（config.secret_env）；缺省则生成一份持久化到 data/.auth_secret，
    保证跨重启签发的 cookie 仍有效，同时不把密钥写进配置文件。"""
    env_name = auth_cfg.get("secret_env", "A2A_UI_SECRET")
    from_env = os.getenv(env_name, "").strip()
    if from_env:
        return from_env
    secret_file = auth_cfg.get("secret_file") or (data_dir / ".auth_secret")
    secret_file = Path(secret_file)
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    if secret_file.exists():
        existing = secret_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    generated = secrets.token_hex(32)
    secret_file.write_text(generated, encoding="utf-8")
    return generated


@dataclass(frozen=True)
class AuthContext:
    enabled: bool
    store: Any  # UserStore(SQLite) 或 MySQLUserStore；同接口 create_user/verify/username
    secret: str
    cookie_name: str
    token_ttl_seconds: float
    registration_enabled: bool = True

    def user_id_from_cookies(self, cookies: dict[str, str]) -> str | None:
        if not self.enabled:
            return None
        token = cookies.get(self.cookie_name)
        if not token:
            return None
        return verify_token(token, self.secret)

    def issue_token(self, user_id: str) -> str:
        return sign_token(user_id, self.secret, ttl_seconds=self.token_ttl_seconds)


def make_auth_context(auth_cfg: dict[str, Any], *, data_dir: Path, repo_root: Path) -> AuthContext:
    """按 config 的 auth 段装配 AuthContext。enabled 默认 True（生产走真实登录）。

    backend=sqlite（默认）→ 本地 SQLite UserStore；backend=mysql → VM MySQL MySQLUserStore
    （装配时顺带建库/建表并灌 seed_users）。
    """
    enabled = bool(auth_cfg.get("enabled", True))
    cookie_name = auth_cfg.get("cookie", "a2a_cs_uid")
    ttl = float(auth_cfg.get("token_ttl_seconds", 604800))
    registration_enabled = bool(auth_cfg.get("registration_enabled", True))
    if not enabled:
        return AuthContext(
            enabled=False, store=None, secret="", cookie_name=cookie_name,
            token_ttl_seconds=ttl, registration_enabled=registration_enabled,
        )

    secret = _resolve_secret(auth_cfg, data_dir)
    backend = auth_cfg.get("backend", "sqlite")

    if backend == "mysql":
        from ui.mysql_store import make_mysql_store

        store = make_mysql_store(
            auth_cfg.get("mysql", {}) or {},
            seed_users=auth_cfg.get("seed_users", []) or [],
        )
        return AuthContext(
            enabled=True, store=store, secret=secret,
            cookie_name=cookie_name, token_ttl_seconds=ttl,
            registration_enabled=registration_enabled,
        )

    db_rel = auth_cfg.get("sqlite_path", "data/users.db")
    db_path = Path(db_rel)
    if not db_path.is_absolute():
        db_path = repo_root / db_path
    return AuthContext(
        enabled=True, store=UserStore(db_path), secret=secret,
        cookie_name=cookie_name, token_ttl_seconds=ttl,
        registration_enabled=registration_enabled,
    )
