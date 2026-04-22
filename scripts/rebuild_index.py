"""Rebuild the SQLite record index from existing JSON records.

Run this once after upgrading from a pre-index version, or whenever
`storage/index.db` appears out of sync with `storage/records/*.json`.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to sys.path so `app` imports resolve when run directly
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.services.storage import StorageService  # noqa: E402
from app.settings import DATA_DIR  # noqa: E402


def main() -> int:
    storage = StorageService(DATA_DIR)
    count = storage.rebuild_index()
    print(f"Rebuilt index with {count} records → {storage._index_db}")  # noqa: SLF001
    return 0


if __name__ == "__main__":
    sys.exit(main())
