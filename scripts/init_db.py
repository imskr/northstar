from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from northstar.db import get_engine  # noqa: E402
from northstar.models import Base  # noqa: E402

Base.metadata.create_all(get_engine())
print("Northstar database schema is ready.")
