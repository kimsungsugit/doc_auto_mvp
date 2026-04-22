from __future__ import annotations

from app.services.pdf_text import TextExtractionResult


def test_upload_rejects_unsupported_file(isolated_app) -> None:
    response = isolated_app.post(
        "/documents/upload",
        files={"file": ("sample.txt", b"hello", "text/plain")},
        data={"uploaded_by": "tester"},
    )
    assert response.status_code == 400


def test_document_workflow_for_text_pdf(isolated_app, isolated_workflow, monkeypatch) -> None:
    monkeypatch.setattr(
        isolated_workflow.pdf_text_service,
        "extract",
        lambda _path: TextExtractionResult(
            text="\n".join(
                [
                    "전자세금계산서",
                    "작성일자 2026-04-06",
                    "공급자 123-45-67890 ABC상사",
                    "공급받는자 234-56-78901 테스트고객",
                    "공급가액 100,000",
                    "세액 10,000",
                    "합계 110,000",
                    "품목 그룹웨어 개발",
                ]
            ),
            requires_ocr=False,
            warnings=[],
        ),
    )

    upload_response = isolated_app.post(
        "/documents/upload",
        files={"file": ("invoice.pdf", b"%PDF-1.4 test", "application/pdf")},
        data={"uploaded_by": "tester"},
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["original_extension"] == ".pdf"
    document_id = upload_response.json()["document_id"]

    extract_response = isolated_app.post(f"/documents/{document_id}/extract")
    assert extract_response.status_code == 200
    assert extract_response.json()["extraction_status"] == "Needs Review"
    assert extract_response.json()["item_count"] == 1
    assert extract_response.json()["document_type"] == "전자세금계산서"
    assert extract_response.json()["document_schema"] == "전자세금계산서"

    fields_response = isolated_app.get(f"/documents/{document_id}/fields")
    assert fields_response.json()["document_schema"] == "전자세금계산서"
    fields = {field["field_name"]: field for field in fields_response.json()["fields"]}
    assert fields["document_type"]["value"] == "전자세금계산서"
    assert fields["issue_date"]["value"] == "2026-04-06"

    update_response = isolated_app.patch(
        f"/documents/{document_id}/fields",
        json={
            "field_name": "supplier_name",
            "value": "ABC상사",
            "updated_by": "reviewer",
            "comment": "수동 보정",
        },
    )
    assert update_response.status_code == 200

    blocked_export = isolated_app.post(f"/documents/{document_id}/export")
    assert blocked_export.status_code == 400

    review_response = isolated_app.post(f"/documents/{document_id}/review")
    assert review_response.status_code == 200
    assert review_response.json()["review_status"] == "Reviewed"

    export_response = isolated_app.post(f"/documents/{document_id}/export")
    assert export_response.status_code == 200
    assert export_response.json()["export_file_name"].endswith(".xlsx")

    logs_response = isolated_app.get(f"/documents/{document_id}/logs")
    assert logs_response.status_code == 200
    assert len(logs_response.json()["processing_logs"]) >= 2

    list_response = isolated_app.get("/documents?query=ABC상사&status=Exported&sort=warning_desc&limit=20")
    assert list_response.status_code == 200
    assert any(item["document_id"] == document_id for item in list_response.json()["items"])


def test_image_upload_uses_ocr(isolated_app, isolated_workflow, monkeypatch) -> None:
    monkeypatch.setattr(
        isolated_workflow.ocr_service,
        "extract_image",
        lambda _path: "\n".join(
            [
                "거래명세서",
                "작성일자 2026-04-20",
                "공급자 301-11-11111 이미지상사",
                "공급받는자 401-22-22222 이미지고객",
                "공급가액 500,000",
                "세액 50,000",
                "합계 550,000",
            ]
        ),
    )

    upload_response = isolated_app.post(
        "/documents/upload",
        files={"file": ("scan.png", b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes", "image/png")},
        data={"uploaded_by": "ocr-user"},
    )
    assert upload_response.status_code == 200
    assert upload_response.json()["original_extension"] == ".png"
    document_id = upload_response.json()["document_id"]

    extract_response = isolated_app.post(f"/documents/{document_id}/extract")
    assert extract_response.status_code == 200
    assert extract_response.json()["requires_ocr"] is True
    assert extract_response.json()["document_type"] == "거래명세서"

    fields_response = isolated_app.get(f"/documents/{document_id}/fields")
    fields = {field["field_name"]: field for field in fields_response.json()["fields"]}
    assert fields["supplier_name"]["value"] == "이미지상사"
    assert fields["document_type"]["value"] == "거래명세서"


def test_batch_upload_accepts_multiple_files(isolated_app) -> None:
    response = isolated_app.post(
        "/documents/upload/batch",
        files=[
            ("files", ("a.pdf", b"%PDF-1.4 a", "application/pdf")),
            ("files", ("b.jpg", b"\xff\xd8\xff" + b"fake-jpg", "image/jpeg")),
        ],
        data={"uploaded_by": "batch-user"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["original_extension"] == ".pdf"
    assert payload[1]["original_extension"] == ".jpg"


def test_document_workflow_for_ocr_pdf(isolated_app, isolated_workflow, monkeypatch) -> None:
    monkeypatch.setattr(
        isolated_workflow.pdf_text_service,
        "extract",
        lambda _path: TextExtractionResult(
            text="",
            requires_ocr=True,
            warnings=["텍스트를 직접 추출하지 못했습니다. OCR 엔진이 필요합니다."],
        ),
    )
    monkeypatch.setattr(
        isolated_workflow.ocr_service,
        "extract",
        lambda _path: "\n".join(
            [
                "전자세금계산서",
                "작성일자 2026-04-11",
                "공급자 301-11-11111 OCR상사",
                "공급받는자 401-22-22222 OCR고객",
                "공급가액 500,000",
                "세액 50,000",
                "합계 550,000",
            ]
        ),
    )

    upload_response = isolated_app.post(
        "/documents/upload",
        files={"file": ("scan.pdf", b"%PDF-1.4 image-like", "application/pdf")},
        data={"uploaded_by": "ocr-user"},
    )
    document_id = upload_response.json()["document_id"]

    extract_response = isolated_app.post(f"/documents/{document_id}/extract")
    assert extract_response.status_code == 200
    assert extract_response.json()["requires_ocr"] is True

    fields_response = isolated_app.get(f"/documents/{document_id}/fields")
    fields = {field["field_name"]: field for field in fields_response.json()["fields"]}
    assert fields["supplier_name"]["value"] == "OCR상사"
    assert fields["total_amount"]["value"] == "550,000"


def test_dashboard_endpoint_returns_recent_and_failed_documents(isolated_app, isolated_workflow, monkeypatch) -> None:
    monkeypatch.setattr(
        isolated_workflow.pdf_text_service,
        "extract",
        lambda _path: TextExtractionResult(
            text="",
            requires_ocr=True,
            warnings=["텍스트를 직접 추출하지 못했습니다. OCR 엔진이 필요합니다."],
        ),
    )

    def raise_ocr_error(_path):
        from app.services.ocr import OcrUnavailableError

        raise OcrUnavailableError("테스트용 OCR 실패")

    monkeypatch.setattr(isolated_workflow.ocr_service, "extract", raise_ocr_error)

    upload_response = isolated_app.post(
        "/documents/upload",
        files={"file": ("broken.pdf", b"%PDF-1.4 broken", "application/pdf")},
        data={"uploaded_by": "ops-user"},
    )
    document_id = upload_response.json()["document_id"]

    failed_extract = isolated_app.post(f"/documents/{document_id}/extract")
    assert failed_extract.status_code == 200

    dashboard_response = isolated_app.get("/documents/dashboard")
    payload = dashboard_response.json()
    assert payload["total_documents"] >= 1
    assert payload["failed_documents"] >= 1
    assert isinstance(payload["recent_documents"], list)
    assert isinstance(payload["failed_recent_documents"], list)


def test_retry_and_ocr_health_endpoints(isolated_app, isolated_workflow, isolated_storage, monkeypatch) -> None:
    call_count = {"count": 0}

    monkeypatch.setattr(
        isolated_workflow.pdf_text_service,
        "extract",
        lambda _path: TextExtractionResult(
            text="",
            requires_ocr=True,
            warnings=["OCR 필요"],
        ),
    )

    def flaky_ocr(_path):
        call_count["count"] += 1
        if call_count["count"] == 1:
            from app.services.ocr import OcrUnavailableError

            raise OcrUnavailableError("첫 시도 실패")
        return "\n".join(
            [
                "전자세금계산서",
                "작성일자 2026-04-19",
                "공급자 130-10-10101 복구상사",
                "공급받는자 241-11-11111 복구고객",
                "공급가액 120,000",
                "세액 12,000",
                "합계 132,000",
            ]
        )

    monkeypatch.setattr(isolated_workflow.ocr_service, "extract", flaky_ocr)
    monkeypatch.setattr(isolated_workflow.ocr_service, "_resolve_tesseract_path", lambda: "C:/Tesseract/tesseract.exe")
    monkeypatch.setattr(isolated_workflow.ocr_service, "_resolve_ghostscript_path", lambda: "C:/gs/gswin64c.exe")

    upload_response = isolated_app.post(
        "/documents/upload",
        files={"file": ("retry.pdf", b"%PDF-1.4 retry", "application/pdf")},
        data={"uploaded_by": "ops-user"},
    )
    document_id = upload_response.json()["document_id"]

    first_extract = isolated_app.post(f"/documents/{document_id}/extract")
    assert first_extract.status_code == 200
    assert first_extract.json()["extraction_status"] == "Failed"

    retry_response = isolated_app.post(f"/documents/{document_id}/retry")
    assert retry_response.status_code == 200
    assert retry_response.json()["extraction_status"] == "Needs Review"
    assert isolated_storage.load_record(document_id).retry_count >= 1

    health_response = isolated_app.get("/system/ocr-health")
    health = health_response.json()
    assert health["ready"] is True
    assert health["tesseract_configured"] is True
    assert health["ghostscript_configured"] is True


def test_readiness_endpoint_returns_preflight_status(isolated_app, isolated_workflow, monkeypatch) -> None:
    monkeypatch.setattr(isolated_workflow.ocr_service, "_resolve_tesseract_path", lambda: "C:/Tesseract/tesseract.exe")
    monkeypatch.setattr(isolated_workflow.ocr_service, "_resolve_ghostscript_path", lambda: "C:/gs/gswin64c.exe")

    response = isolated_app.get("/system/readiness")
    payload = response.json()
    assert payload["template_ready"] is True
    assert payload["sample_case_count"] >= 19
    assert payload["generated_pdf_count"] >= 18
    assert isinstance(payload["checklist"], list)
