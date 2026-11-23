"""Start the packaged desktop app away from the checkout, query it, and stop it."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


def smoke(binary: Path) -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, DULUS_BAR_HOME=tmp, DULUS_BAR_PORT=str(port),
                   DULUS_BAR_SERVER=f"ws://127.0.0.1:{port}",
                   DULUS_BAR_NATIVE_REQUIRED="1", DULUS_BAR_NATIVE_AUTOBUILD="0")
        env.pop("DULUS_BAR_NATIVE_BINARY", None)
        env.pop("DULUS_BAR_FORCE_QT", None)
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
        subprocess.run([binary, "--version"], cwd=tmp, env=env, check=True, timeout=90)
        app = subprocess.Popen([binary, "run", "--foreground"], cwd=tmp, env=env)
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if app.poll() is not None:
                    raise RuntimeError(f"{binary} exited during startup ({app.returncode})")
                if (Path(tmp) / "hub.json").is_file():
                    result = subprocess.run([binary, "status", "--json"], cwd=tmp, env=env,
                                            stdout=subprocess.PIPE, timeout=90, check=True)
                    # The Qt island lives in the hub process. On macOS the
                    # separately packaged Swift app must actually connect.
                    if sys.platform != "darwin" or json.loads(result.stdout)["server"]["uis_connected"]:
                        break
                time.sleep(0.25)
            else:
                raise TimeoutError(f"{binary} did not bring up its island")
            subprocess.run([binary, "quit"], cwd=tmp, env=env, timeout=90, check=True)
            if app.wait(timeout=30) != 0:
                raise RuntimeError(f"{binary} exited with {app.returncode}")
        finally:
            if app.poll() is None:
                app.kill()
                app.wait()


if __name__ == "__main__":
    smoke(Path(sys.argv[1]).resolve(strict=True))
