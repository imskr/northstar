from __future__ import annotations

import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from northstar import create_app  # noqa: E402


def free_port(start: int = 8000) -> int:
    for port in range(start, start + 50):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


port = free_port(int(os.getenv("PORT", "8000")))
url = f"http://localhost:{port}"
print(f"Northstar running at {url}")
print("Data: Turso Cloud" if os.getenv("TURSO_DATABASE_URL") else "Data: local SQLite (data/northstar.db)")
print("Keep this Terminal window open. Press Control-C to stop.")
threading.Timer(0.6, lambda: webbrowser.open(url)).start()
create_app().run(host="127.0.0.1", port=port, debug=False, threaded=True)
