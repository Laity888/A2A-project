"""一键启动入口 src/main.py 的轻量单测（不真起服务子进程）。"""

from __future__ import annotations

import socket

import main


def test_services_cover_all_four():
    joined = " ".join(" ".join(a) for _n, a, _p in main.SERVICES)
    for target in ("agents.retrieval", "agents.reply", "agents.reception", "ui"):
        assert target in joined
    ports = {port for _n, _a, port in main.SERVICES}
    assert ports == {8002, 8003, 8001, 8080}


def test_retrieval_is_first_for_readiness_gate():
    assert "agents.retrieval" in " ".join(main.SERVICES[0][1])


def test_child_env_sets_pythonpath_and_local():
    env = main.child_env(base={"PATH": "/x"})
    assert str(main.SRC) in env["PYTHONPATH"]
    assert env["A2A_LOCAL"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_child_env_does_not_override_existing():
    env = main.child_env(base={"A2A_LOCAL": "0", "PYTHONPATH": "/keep"})
    assert env["A2A_LOCAL"] == "0"
    assert env["PYTHONPATH"].startswith(str(main.SRC))
    assert env["PYTHONPATH"].endswith("/keep")


def test_wait_port_true_on_open_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert main._wait_port("127.0.0.1", port, timeout=2.0) is True


def test_wait_port_false_on_closed_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert main._wait_port("127.0.0.1", port, timeout=0.6, interval=0.2) is False


class _FakeProc:
    def __init__(self, rc=None):
        self._rc = rc

    def poll(self):
        return self._rc


def test_wait_ready_ok_when_port_open_and_alive():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        status, rc = main._wait_ready("x", port, _FakeProc(rc=None), timeout=2.0)
        assert status == "ready" and rc is None


def test_wait_ready_detects_exited_process():
    # 进程已退出（poll 返回码）→ 立即 exited，不干等端口
    status, rc = main._wait_ready("x", 59999, _FakeProc(rc=3), timeout=2.0)
    assert status == "exited" and rc == 3


def test_wait_ready_timeout_when_port_closed_and_alive():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    status, rc = main._wait_ready("x", port, _FakeProc(rc=None), timeout=0.5, interval=0.2)
    assert status == "timeout" and rc is None
