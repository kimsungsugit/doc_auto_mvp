from __future__ import annotations

import json
import logging

import pytest

from app.services.feedback_collector import FeedbackCollector
from app.services.storage import StorageService


class TestStorageCorruptRecord:
    """list_records() should skip corrupt JSON files and log a warning."""

    def test_corrupt_json_skipped(self, tmp_path, caplog):
        storage = StorageService(tmp_path)

        # Two valid records — both indexed
        keep = storage.create_record("good.pdf", "user", 100)
        damaged = storage.create_record("bad.pdf", "user", 100)

        # Corrupt the second record's JSON file (index still references it)
        storage.record_path(damaged.document_id).write_text("{invalid json", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            records, total = storage.list_records()

        ids = {r.document_id for r in records}
        assert keep.document_id in ids
        assert damaged.document_id not in ids
        assert any(damaged.document_id in msg for msg in caplog.messages)

    def test_valid_records_unaffected(self, tmp_path):
        storage = StorageService(tmp_path)
        r1 = storage.create_record("a.pdf", "user", 100)
        r2 = storage.create_record("b.pdf", "user", 200)

        records, _ = storage.list_records()
        ids = {r.document_id for r in records}
        assert r1.document_id in ids
        assert r2.document_id in ids


class TestFeedbackCollectorErrorHandling:
    """feedback_collector should log warnings on I/O errors, not crash."""

    def test_corrupt_feedback_file_returns_empty(self, tmp_path, caplog):
        collector = FeedbackCollector(tmp_path)
        feedback_path = tmp_path / "feedback" / "corrections.json"
        feedback_path.write_text("not valid json", encoding="utf-8")

        with caplog.at_level(logging.WARNING):
            stats = collector.get_accuracy_stats()

        assert stats["total_corrections"] == 0
        assert any("Failed to load feedback" in msg for msg in caplog.messages)

    def test_collect_correction_logs_on_write_failure(self, tmp_path, caplog, monkeypatch):
        collector = FeedbackCollector(tmp_path)

        # Make _save_file raise OSError
        def raise_os_error(_path, _entries):
            raise OSError("disk full")

        monkeypatch.setattr(collector, "_save_file", raise_os_error)

        with caplog.at_level(logging.WARNING):
            # Should not raise
            collector.collect_correction(
                document_id="doc1",
                document_type="전자세금계산서",
                field_name="supplier_name",
                ai_value="old",
                corrected_value="new",
                extraction_source="ai",
            )

        assert any("Failed to save feedback" in msg for msg in caplog.messages)


class TestUploadSizeLimit:
    """Upload endpoints should reject files exceeding MAX_UPLOAD_SIZE."""

    def test_upload_oversized_file_returns_413(self, isolated_app, monkeypatch):
        monkeypatch.setattr("app.main.MAX_UPLOAD_SIZE", 100)  # 100 bytes limit

        large_payload = b"x" * 200
        response = isolated_app.post(
            "/documents/upload",
            files={"file": ("big.pdf", large_payload, "application/pdf")},
            data={"uploaded_by": "tester"},
        )
        assert response.status_code == 413

    def test_upload_within_limit_succeeds(self, isolated_app, monkeypatch):
        monkeypatch.setattr("app.main.MAX_UPLOAD_SIZE", 1000)

        small_payload = b"%PDF-" + b"x" * 95  # valid PDF magic prefix
        response = isolated_app.post(
            "/documents/upload",
            files={"file": ("small.pdf", small_payload, "application/pdf")},
            data={"uploaded_by": "tester"},
        )
        assert response.status_code == 200

    def test_batch_upload_oversized_file_returns_413(self, isolated_app, monkeypatch):
        monkeypatch.setattr("app.main.MAX_UPLOAD_SIZE", 100)

        large_payload = b"x" * 200
        response = isolated_app.post(
            "/documents/upload/batch",
            files=[("files", ("big.pdf", large_payload, "application/pdf"))],
            data={"uploaded_by": "tester"},
        )
        assert response.status_code == 413
