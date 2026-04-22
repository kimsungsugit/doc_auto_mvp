from __future__ import annotations

import logging
import sqlite3

from app.services.storage import StorageService


class TestSaveRecordResilience:
    """save_record should not raise even if SQL index write fails — JSON is source of truth."""

    def test_sql_failure_logs_warning_and_keeps_json(self, tmp_path, monkeypatch, caplog):
        storage = StorageService(tmp_path)
        record = storage.create_record("test.pdf", "user", 10)

        def raise_sql(_record):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(storage, "_upsert_index", raise_sql)

        with caplog.at_level(logging.WARNING):
            # Should not raise even though SQL fails
            storage.save_record(record)

        assert storage.record_path(record.document_id).exists()
        assert any("Index upsert failed" in msg for msg in caplog.messages)

    def test_rebuild_recovers_from_missing_index_rows(self, tmp_path):
        storage = StorageService(tmp_path)
        storage.create_record("a.pdf", "user", 10)
        b = storage.create_record("b.pdf", "user", 10)

        # Delete b's index row directly
        with storage._connect() as conn:  # noqa: SLF001
            conn.execute("DELETE FROM records WHERE document_id = ?", (b.document_id,))

        _, total_before = storage.list_records()
        assert total_before == 1

        restored = storage.rebuild_index()
        assert restored == 2

        _, total_after = storage.list_records()
        assert total_after == 2
