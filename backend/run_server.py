import multiprocessing
import os
import threading
from pathlib import Path

import uvicorn
from app.main import app


def exit_when_parent_stops(server) -> None:
    """Ensure the bundled backend never survives its desktop parent."""
    parent_pid = int(os.environ.get("YUS_AI_PARENT_PID", "0"))
    if not parent_pid or os.name != "nt":
        return

    import ctypes

    synchronize = 0x00100000
    infinite = 0xFFFFFFFF
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        server.should_exit = True
        return
    ctypes.windll.kernel32.WaitForSingleObject(handle, infinite)
    ctypes.windll.kernel32.CloseHandle(handle)
    server.should_exit = True


def watch_shutdown_request(server) -> None:
    shutdown_file = os.environ.get("YUS_AI_SHUTDOWN_FILE")
    if not shutdown_file:
        return
    signal = Path(shutdown_file)
    while not server.should_exit:
        if signal.exists():
            signal.unlink(missing_ok=True)
            server.should_exit = True
            return
        threading.Event().wait(0.2)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning", timeout_graceful_shutdown=2))
    threading.Thread(target=exit_when_parent_stops, args=(server,), daemon=True).start()
    threading.Thread(target=watch_shutdown_request, args=(server,), daemon=True).start()
    server.run()
