import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace


def test_shutdown_signal_requests_graceful_exit(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("backend_launcher", Path(__file__).parents[1] / "run_server.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    signal = tmp_path / "backend.shutdown"
    monkeypatch.setenv("YUS_AI_SHUTDOWN_FILE", str(signal))
    server = SimpleNamespace(should_exit=False)
    worker = threading.Thread(target=launcher.watch_shutdown_request, args=(server,))
    worker.start()
    try:
        signal.touch()
        worker.join(timeout=2)
        assert server.should_exit
        assert not signal.exists()
        assert not worker.is_alive()
    finally:
        server.should_exit = True
        worker.join(timeout=2)


def test_no_shutdown_path_does_not_stop_server(monkeypatch):
    spec = importlib.util.spec_from_file_location("backend_launcher", Path(__file__).parents[1] / "run_server.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    monkeypatch.delenv("YUS_AI_SHUTDOWN_FILE", raising=False)
    server = SimpleNamespace(should_exit=False)
    launcher.watch_shutdown_request(server)
    assert not server.should_exit
