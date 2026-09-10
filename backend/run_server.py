import multiprocessing
import os
import threading

import uvicorn
from app.main import app


def exit_when_parent_stops() -> None:
    """Ensure the bundled backend never survives its desktop parent."""
    parent_pid = int(os.environ.get("YUS_AI_PARENT_PID", "0"))
    if not parent_pid or os.name != "nt":
        return

    import ctypes

    synchronize = 0x00100000
    infinite = 0xFFFFFFFF
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        os._exit(0)
    ctypes.windll.kernel32.WaitForSingleObject(handle, infinite)
    ctypes.windll.kernel32.CloseHandle(handle)
    os._exit(0)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    threading.Thread(target=exit_when_parent_stops, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
