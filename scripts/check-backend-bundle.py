"""Read-only layout/archive check plus isolated boot smoke test (no model calls)."""
import os
from pathlib import Path
import socket
import subprocess
import tempfile

from PyInstaller.archive.readers import CArchiveReader

root = Path(__file__).resolve().parents[1]
exe = root / "frontend/src-tauri/binaries/yus-ai-backend-x86_64-pc-windows-msvc.exe"
runtime = exe.parent / "backend-runtime"
assert (runtime / "python310.dll").is_file()
archive = CArchiveReader(str(exe))
assert any("backend-runtime" in option for option in archive.options), archive.options
assert not any(entry[-1] == "b" for entry in archive.toc.values()), "Single-file embedded binaries still present"
print(f"Directory layout verified: executable {exe.stat().st_size / 1024**2:.1f} MiB; dependencies external")

# Never displace the user's service. With port 8000 occupied, successful imports and
# startup must end with the expected address-in-use error, using an isolated E: DB.
with socket.socket() as probe:
    occupied = probe.connect_ex(("127.0.0.1", 8000)) == 0
if not occupied:
    print("Boot smoke test skipped: port 8000 is free (avoid leaving a service running).")
else:
    before = set(Path(tempfile.gettempdir()).glob("_MEI*"))
    with tempfile.TemporaryDirectory(prefix="backend-smoke-", dir=root / "build") as directory:
        env = os.environ.copy()
        env.update(YUS_AI_DATA_DIR=directory, YUS_AI_LOG_DIR=directory,
                   TEMP=directory, TMP=directory, ARGOS_PACKAGES_DIR=str(Path(directory) / "models"),
                   XDG_DATA_HOME=str(Path(directory) / "xdg-data"),
                   XDG_CONFIG_HOME=str(Path(directory) / "xdg-config"),
                   XDG_CACHE_HOME=str(Path(directory) / "xdg-cache"), ARGOS_CHUNK_TYPE="MINISBD")
        env.pop("YUS_AI_PARENT_PID", None)
        env.pop("YUS_AI_SHUTDOWN_FILE", None)
        result = subprocess.run([str(exe)], env=env, capture_output=True, timeout=30)
        assert result.returncode != 0, "Expected occupied port"
        assert b"10048" in result.stderr or b"address already in use" in result.stderr.lower(), result.stderr.decode(errors="replace")
        log = (Path(directory) / "yus-ai.log").read_text(encoding="utf-8")
        assert "backend_started version=0.13.3" in log
        assert "backend_stopped" in log
        assert not list(Path(directory).glob("_MEI*"))
        assert set(Path(tempfile.gettempdir()).glob("_MEI*")) - before == set()
        print("Packaged backend imports/startup verified; expected occupied-port exit; no _MEI extraction")
