from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from northstar.db import initialize_database  # noqa: E402

initialize_database()
print("Northstar database schema is ready.")
