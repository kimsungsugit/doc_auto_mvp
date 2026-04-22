from __future__ import annotations

import io

from app.models import DocumentStatus


def _upload_and_seed(client, storage, *, content: bytes = b"%PDF-1.4 stub") -> str:
    """Upload a PDF stub and manually populate extracted fields for review tests."""
    resp = client.post(
        "/documents/upload",
        files={"file": ("x.pdf", io.BytesIO(content), "application/pdf")},
        data={"uploaded_by": "tester"},
    )
    assert resp.status_code == 200
    doc_id = resp.json()["document_id"]
    return doc_id


class TestRecExportAllowed:
    """#4: 엑셀 재내보내기 — Exported 상태에서도 다시 생성 가능."""

    def test_export_allowed_from_exported_state(self, isolated_workflow, isolated_storage, monkeypatch, tmp_path):
        from app.services.pdf_text import TextExtractionResult

        # Seed a PDF and mock text extraction
        doc_id = _upload_pdf_to_storage(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _: TextExtractionResult(
                text="\n".join([
                    "전자세금계산서", "작성일자 2024-05-01",
                    "공급자 123-45-67890 공급사", "공급받는자 234-56-78901 구매사",
                    "공급가액 1,000 세액 100 합계 1,100", "품목 서비스",
                ]),
                requires_ocr=False, warnings=[],
            ),
        )
        isolated_workflow.extract(doc_id)
        record, _ = isolated_workflow.finalize_review(doc_id)
        assert record.status == DocumentStatus.REVIEWED

        # First export → EXPORTED
        record = isolated_workflow.export(doc_id)
        assert record.status == DocumentStatus.EXPORTED

        # Second export from EXPORTED → should succeed (idempotent re-export)
        record = isolated_workflow.export(doc_id)
        assert record.status == DocumentStatus.EXPORTED


class TestReclassifyEndpoint:
    """#1: 수동 재분류 — forced_type으로 분류 덮어쓰기."""

    def test_supported_types_endpoint(self, isolated_app):
        resp = isolated_app.get("/system/document-types")
        assert resp.status_code == 200
        data = resp.json()
        assert "영수증" in data["types"]
        assert "전자세금계산서" in data["types"]

    def test_reclassify_changes_document_type(self, isolated_app, isolated_storage, isolated_workflow, monkeypatch):
        from app.services.pdf_text import TextExtractionResult

        doc_id = _upload_pdf_to_storage(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _: TextExtractionResult(
                text="애매한 문서. 영수증도 세금계산서도 아닌 텍스트.",
                requires_ocr=False, warnings=[],
            ),
        )
        isolated_workflow.extract(doc_id)

        resp = isolated_app.post(f"/documents/{doc_id}/reclassify?document_type=영수증")
        assert resp.status_code == 200
        assert resp.json()["document_type"] == "영수증"

    def test_reclassify_adds_type_specific_fields(self, isolated_app, isolated_storage, isolated_workflow, monkeypatch):
        """재분류 후 영수증 전용 필드(approval_no 등)가 record.fields에 포함되어야 함."""
        from app.services.pdf_text import TextExtractionResult

        doc_id = _upload_pdf_to_storage(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _: TextExtractionResult(
                text="전자세금계산서 예시 텍스트 공급가액 1,000",
                requires_ocr=False, warnings=[],
            ),
        )
        isolated_workflow.extract(doc_id)

        # 재분류 전: 영수증 필드 없음 (전자세금계산서 스키마)
        pre = isolated_app.get(f"/documents/{doc_id}/fields").json()
        pre_names = {f["field_name"] for f in pre["fields"]}
        assert "approval_no" not in pre_names

        # 재분류 후: 영수증 전용 필드 포함
        isolated_app.post(f"/documents/{doc_id}/reclassify?document_type=영수증")
        post = isolated_app.get(f"/documents/{doc_id}/fields").json()
        post_names = {f["field_name"] for f in post["fields"]}
        assert "approval_no" in post_names
        assert "card_number_masked" in post_names
        assert "transaction_time" in post_names

    def test_reclassify_rejects_unknown_type(self, isolated_app, isolated_storage):
        doc_id = _upload_pdf_to_storage(isolated_storage)
        resp = isolated_app.post(f"/documents/{doc_id}/reclassify?document_type=허위유형")
        assert resp.status_code == 400


class TestBatchReview:
    """#3: 검수 완료 일괄 처리."""

    def test_batch_review_finalizes_multiple_docs(self, isolated_app, isolated_storage, isolated_workflow, monkeypatch):
        from app.services.pdf_text import TextExtractionResult

        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _: TextExtractionResult(
                text="\n".join([
                    "전자세금계산서", "작성일자 2024-05-01",
                    "공급자 111-11-11111 공급사", "공급받는자 222-22-22222 구매사",
                    "공급가액 10,000 세액 1,000 합계 11,000", "품목 컨설팅",
                ]),
                requires_ocr=False, warnings=[],
            ),
        )

        ids = []
        for _ in range(3):
            doc_id = _upload_pdf_to_storage(isolated_storage)
            isolated_workflow.extract(doc_id)
            ids.append(doc_id)

        resp = isolated_app.post("/documents/review/batch", json=ids)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["success"] + data["failed"] == 3

    def test_batch_review_reports_missing_fields(self, isolated_app, isolated_storage, isolated_workflow, monkeypatch):
        from app.services.pdf_text import TextExtractionResult

        doc_id = _upload_pdf_to_storage(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _: TextExtractionResult(text="빈 텍스트", requires_ocr=False, warnings=[]),
        )
        isolated_workflow.extract(doc_id)

        resp = isolated_app.post("/documents/review/batch", json=[doc_id])
        data = resp.json()
        assert data["total"] == 1
        # 카운트 분리 검증: success + missing_fields + failed = total
        assert data["success"] + data["missing_fields"] + data["failed"] == data["total"]
        # 빈 텍스트라 필수 필드 누락 → missing_fields에 카운트
        assert data["missing_fields"] >= 1
        assert data["success"] == 0

    def test_batch_review_counts_sum_to_total(self, isolated_app, isolated_storage, isolated_workflow, monkeypatch):
        """success + missing_fields + failed == total (중복 없음)."""
        from app.services.pdf_text import TextExtractionResult

        good_doc = _upload_pdf_to_storage(isolated_storage)
        monkeypatch.setattr(
            isolated_workflow.pdf_text_service, "extract",
            lambda _: TextExtractionResult(
                text="\n".join([
                    "전자세금계산서", "작성일자 2024-05-01",
                    "공급자 111-11-11111 공급사", "공급받는자 222-22-22222 구매사",
                    "공급가액 10,000 세액 1,000 합계 11,000", "품목 컨설팅",
                ]),
                requires_ocr=False, warnings=[],
            ),
        )
        isolated_workflow.extract(good_doc)

        resp = isolated_app.post("/documents/review/batch", json=[good_doc, "nonexistent123"])
        data = resp.json()
        assert data["total"] == 2
        assert data["success"] + data["missing_fields"] + data["failed"] == 2
        assert data["failed"] >= 1  # nonexistent → FileNotFoundError


def _upload_pdf_to_storage(storage) -> str:
    """Create a record directly via storage (bypasses upload endpoint)."""
    record = storage.create_record("stub.pdf", "tester", 10)
    storage.original_path(record.document_id, ".pdf").write_bytes(b"%PDF-1.4 stub")
    return record.document_id
