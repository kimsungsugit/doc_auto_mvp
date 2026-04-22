"""Sprint 2-B 신규 기능: list_summaries / feedback 롤오버 / last_error UI exposure."""
from __future__ import annotations

from datetime import UTC, datetime

from app.services.storage import StorageService


class TestListSummaries:
    """list_summaries는 JSON 로드 없이 인덱스 DB만으로 DocumentSummary를 생성해야 함."""

    def test_basic_summary_from_index(self, tmp_path):
        storage = StorageService(tmp_path)
        r1 = storage.create_record("a.pdf", "alice", 100)
        r2 = storage.create_record("b.pdf", "bob", 200)

        summaries, total = storage.list_summaries()
        assert total == 2
        ids = {s.document_id for s in summaries}
        assert r1.document_id in ids
        assert r2.document_id in ids

    def test_summary_fields_populated_from_index(self, tmp_path):
        """last_error, requires_ocr, export_file_name, item_count 모두 인덱스에서 나와야 함."""
        storage = StorageService(tmp_path)
        record = storage.create_record("x.pdf", "user", 100)
        record.last_error = "OCR failed"
        record.requires_ocr = True
        record.export_file_name = "x_report.xlsx"
        storage.save_record(record)

        summaries, _ = storage.list_summaries()
        s = next(s for s in summaries if s.document_id == record.document_id)
        assert s.last_error == "OCR failed"
        assert s.requires_ocr is True
        assert s.export_file_name == "x_report.xlsx"
        assert s.original_extension == ".pdf"
        assert s.item_count == 0

    def test_summaries_does_not_load_json_files(self, tmp_path, monkeypatch):
        """load_record가 호출되지 않음을 검증 — 목적은 파일 IO 회피."""
        storage = StorageService(tmp_path)
        storage.create_record("a.pdf", "u", 100)

        load_calls: list[str] = []
        original_load = storage.load_record
        def spy_load(doc_id):
            load_calls.append(doc_id)
            return original_load(doc_id)
        monkeypatch.setattr(storage, "load_record", spy_load)

        storage.list_summaries(limit=10)
        assert load_calls == []  # list_summaries는 load_record를 절대 호출하면 안 됨

    def test_status_filter_applies(self, tmp_path):
        storage = StorageService(tmp_path)
        r1 = storage.create_record("a.pdf", "u", 100)
        r2 = storage.create_record("b.pdf", "u", 100)
        r2_loaded = storage.load_record(r2.document_id)
        r2_loaded.status = r2_loaded.status.__class__.FAILED  # type: ignore[attr-defined]
        storage.save_record(r2_loaded)

        failed, _ = storage.list_summaries(status="Failed")
        assert [s.document_id for s in failed] == [r2.document_id]

        uploaded, _ = storage.list_summaries(status="Uploaded")
        assert r1.document_id in [s.document_id for s in uploaded]


class TestSchemaMigration:
    """기존 index.db에 새 컬럼이 없어도 자동 ALTER TABLE로 보강되어야 함."""

    def test_migration_adds_missing_columns(self, tmp_path):
        import sqlite3
        # 구버전 스키마로 수동 생성
        db_path = tmp_path / "index.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE records (document_id TEXT PRIMARY KEY, uploaded_at REAL, "
            "status TEXT, uploaded_by TEXT, original_file_name TEXT, "
            "warning_count INTEGER, searchable_text TEXT)"
        )
        conn.commit()
        conn.close()

        # StorageService 초기화 → 마이그레이션 자동 실행
        storage = StorageService(tmp_path)

        with storage._connect() as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(records)")}
        assert "last_error" in cols
        assert "requires_ocr" in cols
        assert "export_file_name" in cols
        assert "item_count" in cols
        assert "original_extension" in cols
        # 저장된 레코드가 있으면 list_summaries 정상 동작하는지
        storage.create_record("x.pdf", "u", 100)
        summaries, _ = storage.list_summaries()
        assert len(summaries) == 1


class TestFeedbackRollover:
    """Feedback Collector가 일 단위로 파일을 분리 저장."""

    def test_writes_to_day_file(self, tmp_path):
        from app.services.feedback_collector import FeedbackCollector
        collector = FeedbackCollector(tmp_path)
        collector.collect_correction(
            document_id="abc123def456", document_type="영수증", field_name="supplier_name",
            ai_value="old", corrected_value="new", extraction_source="ai",
        )

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        day_file = tmp_path / "feedback" / f"corrections_{today}.json"
        assert day_file.exists(), f"Expected day file {day_file.name}"

    def test_legacy_file_still_counted(self, tmp_path):
        """기존 corrections.json이 있으면 집계에 포함되어야 함."""
        import json
        from app.services.feedback_collector import FeedbackCollector

        feedback_dir = tmp_path / "feedback"
        feedback_dir.mkdir()
        legacy = feedback_dir / "corrections.json"
        legacy.write_text(json.dumps([{
            "timestamp": "2025-01-01T00:00:00+00:00",
            "document_id": "abc",
            "document_type": "영수증",
            "field_name": "supplier_name",
            "ai_value": "a",
            "corrected_value": "b",
            "extraction_source": "ai",
        }]), encoding="utf-8")

        collector = FeedbackCollector(tmp_path)
        # 새 엔트리 추가 (오늘 파일에 저장)
        collector.collect_correction(
            document_id="def", document_type="영수증", field_name="supplier_biz_no",
            ai_value="111", corrected_value="222", extraction_source="ai",
        )

        stats = collector.get_accuracy_stats()
        assert stats["total_corrections"] == 2  # 레거시 1 + 오늘 1
