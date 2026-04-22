from __future__ import annotations

from app.services.storage import StorageService


class TestStoragePagination:
    """list_records() offset/limit pagination."""

    def test_offset_skips_records(self, tmp_path):
        storage = StorageService(tmp_path)
        ids = []
        for i in range(5):
            record = storage.create_record(f"file{i}.pdf", "user", 100)
            ids.append(record.document_id)

        all_records, total = storage.list_records(limit=100)
        assert len(all_records) == 5
        assert total == 5

        page, page_total = storage.list_records(limit=2, offset=2)
        assert len(page) == 2
        assert page_total == 5

    def test_offset_beyond_total_returns_empty(self, tmp_path):
        storage = StorageService(tmp_path)
        storage.create_record("a.pdf", "user", 100)

        records, total = storage.list_records(offset=10)
        assert records == []
        assert total == 1

    def test_count_records(self, tmp_path):
        storage = StorageService(tmp_path)
        assert storage.count_records() == 0

        storage.create_record("a.pdf", "user", 100)
        storage.create_record("b.pdf", "user", 200)
        assert storage.count_records() == 2

    def test_paginated_api_response(self, isolated_app):
        """GET /documents returns paginated response with items/total/offset/limit."""
        # Upload a document first
        isolated_app.post(
            "/documents/upload",
            files={"file": ("test.pdf", b"%PDF-1.4 dummy", "application/pdf")},
        )

        response = isolated_app.get("/documents")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data
        assert len(data["items"]) == 1
        assert data["total"] >= 1

    def test_paginated_api_with_offset(self, isolated_app):
        """GET /documents?offset=N skips records."""
        for i in range(3):
            isolated_app.post(
                "/documents/upload",
                files={"file": (f"test{i}.pdf", b"%PDF-1.4 dummy", "application/pdf")},
            )

        response = isolated_app.get("/documents?limit=2&offset=0")
        data = response.json()
        assert len(data["items"]) == 2

        response = isolated_app.get("/documents?limit=2&offset=2")
        data = response.json()
        assert len(data["items"]) == 1
