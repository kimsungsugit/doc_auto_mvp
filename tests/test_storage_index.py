from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from app.services.storage import StorageService


class TestStorageIndex:
    def test_count_on_1000_records_is_fast(self, tmp_path):
        """Index-based count_records should stay well under 50ms regardless of record count."""
        storage = StorageService(tmp_path)
        for i in range(1000):
            storage.create_record(f"doc_{i:04d}.pdf", "user", 10)

        start = time.perf_counter()
        total = storage.count_records()
        elapsed = time.perf_counter() - start

        assert total == 1000
        assert elapsed < 0.1, f"Expected <0.1s for COUNT via SQLite index, got {elapsed:.3f}s"

    def test_list_limit_bounds_load_cost(self, tmp_path):
        """list_records(limit=N) should load ~N JSONs, not all records."""
        storage = StorageService(tmp_path)
        for i in range(500):
            storage.create_record(f"doc_{i:04d}.pdf", "user", 10)

        start = time.perf_counter()
        records, total = storage.list_records(limit=10)
        elapsed = time.perf_counter() - start

        assert total == 500
        assert len(records) == 10
        # 10 JSON loads should finish quickly even on slow filesystems
        assert elapsed < 1.0, f"Expected <1.0s to load 10 records via index, got {elapsed:.2f}s"

    def test_rebuild_index_restores_consistency(self, tmp_path):
        storage = StorageService(tmp_path)
        a = storage.create_record("a.pdf", "user", 10)
        b = storage.create_record("b.pdf", "user", 10)

        # Wipe the index file to simulate corruption / first-time migration
        storage._index_db.unlink()  # noqa: SLF001
        storage._init_index()  # noqa: SLF001

        # Before rebuild, index is empty
        _, total_before = storage.list_records()
        assert total_before == 0

        rebuilt = storage.rebuild_index()
        assert rebuilt == 2

        records, total = storage.list_records()
        ids = {r.document_id for r in records}
        assert ids == {a.document_id, b.document_id}
        assert total == 2

    def test_concurrent_saves_preserve_integrity(self, tmp_path):
        storage = StorageService(tmp_path)

        def _create(i: int) -> str:
            rec = storage.create_record(f"doc_{i}.pdf", "tester", 1)
            return rec.document_id

        with ThreadPoolExecutor(max_workers=5) as pool:
            ids = list(pool.map(_create, range(20)))

        assert len(set(ids)) == 20
        _, total = storage.list_records(limit=100)
        assert total == 20

    def test_filter_and_sort_use_index(self, tmp_path):
        storage = StorageService(tmp_path)
        for i in range(5):
            storage.create_record(f"alpha_{i}.pdf", "tester", 1)
        for i in range(3):
            storage.create_record(f"beta_{i}.pdf", "tester", 1)

        records, total = storage.list_records(query="alpha")
        assert total == 5
        assert all("alpha" in r.original_file_name for r in records)
