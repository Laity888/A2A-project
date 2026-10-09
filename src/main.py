"""一键启动全部服务（本地开发入口）。

在 PyCharm 里右键 Run 'main'（或命令行 `python -m main` / `python src/main.py`）即可
同时拉起 检索(8002) / 答复(8003) / 接待(8001) / UI(8080) 四个服务，无需逐个启动；
Ctrl+C 一并停止。

做法：为每个服务 fork 一个子进程跑它原有的 `python -m ...` 启动逻辑，好处是——
- 完全复用各服务现成的 __main__（行为与单独启动一致）；
- 进程隔离：某个服务崩溃/端口占用不影响其它服务，日志各自独立；
- 检索的重模型（BGE-M3+reranker）留在自己进程里，不拖累其它服务。

环境变量：自动给子进程注入 PYTHONPATH=src、A2A_LOCAL=1（把配置里的容器 service-name
URL 改写成 127.0.0.1，本地直跑用）、PYTHONIOENCODING=utf-8。DashScope 密钥仍从 .env /
环境变量 aliyun_API_KEY 读取（各服务自己加载）。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

# (展示名, 传给 python 的参数, 端口)；启动顺序按依赖：检索/答复先起，接待、UI 后起
SERVICES: list[tuple[str, list[str], int]] = [
    ("检索 Retrieval", ["-m", "agents.retrieval"], 8002),
    ("答复 Reply", ["-m", "agents.reply"], 8003),
    ("接待 Reception", ["-m", "agents.reception"], 8001),
    ("UI", ["-m", "ui"], 8080),
]


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """构造子进程环境：补 PYTHONPATH=src、A2A_LOCAL=1、UTF-8 输出；已设的不覆盖。"""
    env = dict(base if base is not None else os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC) + ((os.pathsep + existing) if existing else "")
    env.setdefault("A2A_LOCAL", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _wait_port(host: str, port: int, timeout: float = 120.0, interval: float = 0.5) -> bool:
    """轮询 TCP 连接，端口可连即返回 True（该服务 build_app 完成后才 bind，故连上=已就绪）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return True
            except OSError:
                time.sleep(interval)
    return False


def _wait_ready(name: str, port: int, proc: "subprocess.Popen", timeout: float = 180.0,
                interval: float = 0.5, host: str = "127.0.0.1") -> tuple[str, int | None]:
    """等某服务端口可连=就绪；期间若进程已退出则立即返回，不干等。

    返回 (状态, 退出码)：ready / exited(rc) / timeout(None)。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            return "exited", rc
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return "ready", None
            except OSError:
                pass
        time.sleep(interval)
    return "timeout", None


def main() -> int:
    env = child_env()
    print("=" * 56)
    print(" 一键启动 A2A 智能客服全栈（Ctrl+C 停止全部）")
    print(f" 仓库根：{REPO_ROOT}")
    print(" 提示：先起检索并就绪后再起其余三个，全部就绪才提示打开浏览器；密钥读 .env 的 aliyun_API_KEY")
    print("=" * 56)

    procs: list[tuple[str, int, subprocess.Popen]] = []

    def _spawn(name: str, args: list[str], port: int) -> subprocess.Popen:
        p = subprocess.Popen([sys.executable, *args], cwd=str(REPO_ROOT), env=env)
        procs.append((name, port, p))
        print(f"  · 启动 {name:<16} :{port}  (pid {p.pid})")
        return p

    def _wait_and_report(name: str, port: int, p: subprocess.Popen) -> bool:
        status, rc = _wait_ready(name, port, p, timeout=180.0)
        if status == "ready":
            print(f"  ✓ {name:<16} 就绪 :{port}")
            return True
        if status == "exited":
            print(f"  ! {name}(:{port}) 启动失败（进程已退出 code {rc}）")
        else:
            print(f"  ! {name}(:{port}) 就绪超时（Milvus/模型/端口可能有问题）")
        return False

    all_ready = True

    # 严格串行：先起检索并等它就绪，再起其余三个（保证 8002 的 "running" 日志排在最前）
    r_name, r_args, r_port = SERVICES[0]
    r_proc = _spawn(r_name, r_args, r_port)
    print(f"  … 等待 {r_name} 就绪（加载 BGE-M3，十几~几十秒），就绪后再起其余服务")
    if not _wait_and_report(r_name, r_port, r_proc):
        all_ready = False
        print("  ! 检索未就绪，仍继续启动其余服务（但检索链路会受影响）")

    # 检索就绪（或失败）后，再起其余三个并逐个等就绪
    for name, args, port in SERVICES[1:]:
        _spawn(name, args, port)
    for name, port, p in procs[1:]:
        if not _wait_and_report(name, port, p):
            all_ready = False

    # 3) 只有全部就绪，才提示打开浏览器
    if all_ready:
        print(f"\n 全部就绪 → 浏览器打开 http://127.0.0.1:8080\n")
    else:
        print(f"\n 部分服务未就绪（见上方 ! 行）；可 Ctrl+C 退出排查，或按现状继续运行\n")

    reported: set[int] = set()
    try:
        while True:
            alive = 0
            for name, port, p in procs:
                rc = p.poll()
                if rc is None:
                    alive += 1
                elif id(p) not in reported:
                    reported.add(id(p))
                    print(f"  ! {name}(:{port}) 已退出 (code {rc})")
            if alive == 0:
                print(" 所有服务均已退出。")
                break
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n 正在停止全部服务…")
        for name, port, p in procs:
            if p.poll() is None:
                p.terminate()
        deadline = time.time() + 5
        for name, port, p in procs:
            try:
                p.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                p.kill()
        print(" 已全部停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
