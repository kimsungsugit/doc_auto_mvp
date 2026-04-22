from __future__ import annotations

import io


class TestFileSignatureValidation:
    """업로드 파일이 확장자에 해당하는 매직 바이트로 시작하는지 검증."""

    def test_upload_rejects_pdf_without_magic(self, isolated_app):
        """확장자 .pdf인데 %PDF- 프리픽스 없으면 400."""
        resp = isolated_app.post(
            "/documents/upload",
            files={"file": ("fake.pdf", io.BytesIO(b"just text, not pdf"), "application/pdf")},
            data={"uploaded_by": "tester"},
        )
        assert resp.status_code == 400
        assert "확장자" in resp.json()["detail"]

    def test_upload_rejects_png_without_magic(self, isolated_app):
        resp = isolated_app.post(
            "/documents/upload",
            files={"file": ("fake.png", io.BytesIO(b"not a real png"), "image/png")},
            data={"uploaded_by": "tester"},
        )
        assert resp.status_code == 400

    def test_upload_rejects_jpeg_without_magic(self, isolated_app):
        resp = isolated_app.post(
            "/documents/upload",
            files={"file": ("fake.jpg", io.BytesIO(b"not jpeg"), "image/jpeg")},
            data={"uploaded_by": "tester"},
        )
        assert resp.status_code == 400

    def test_upload_accepts_valid_pdf_magic(self, isolated_app):
        resp = isolated_app.post(
            "/documents/upload",
            files={"file": ("real.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
            data={"uploaded_by": "tester"},
        )
        assert resp.status_code == 200

    def test_upload_accepts_valid_png_magic(self, isolated_app):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"rest of png"
        resp = isolated_app.post(
            "/documents/upload",
            files={"file": ("real.png", io.BytesIO(png_bytes), "image/png")},
            data={"uploaded_by": "tester"},
        )
        assert resp.status_code == 200

    def test_upload_accepts_valid_jpeg_magic(self, isolated_app):
        jpeg_bytes = b"\xff\xd8\xff" + b"rest of jpeg"
        resp = isolated_app.post(
            "/documents/upload",
            files={"file": ("real.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
            data={"uploaded_by": "tester"},
        )
        assert resp.status_code == 200

    def test_batch_upload_rejects_mismatched_content(self, isolated_app):
        resp = isolated_app.post(
            "/documents/upload/batch",
            files=[("files", ("fake.pdf", b"not pdf at all", "application/pdf"))],
            data={"uploaded_by": "tester"},
        )
        assert resp.status_code == 400


class TestDownloadEndpointAuth:
    """/documents/{id}/download는 API_KEY 설정 시 Bearer 토큰을 요구해야 함."""

    def test_download_open_when_api_key_unset(self, isolated_app, monkeypatch):
        """API_KEY 미설정 시 download는 열림(개발 편의). 하지만 부팅 시 경고 로그."""
        monkeypatch.setattr("app.main._API_KEY", "")
        # 파일 없어서 404가 나오지만, 401이 아니어야 함 (인증 통과)
        resp = isolated_app.get("/documents/nonexistent/download")
        assert resp.status_code == 404

    def test_download_requires_bearer_when_api_key_set(self, isolated_app, monkeypatch):
        monkeypatch.setattr("app.main._API_KEY", "test-secret")
        resp = isolated_app.get("/documents/nonexistent/download")
        assert resp.status_code == 401

    def test_download_accepts_valid_bearer(self, isolated_app, monkeypatch):
        monkeypatch.setattr("app.main._API_KEY", "test-secret")
        resp = isolated_app.get(
            "/documents/nonexistent/download",
            headers={"Authorization": "Bearer test-secret"},
        )
        # 파일 없어서 404이지만 401이 아니어야 함
        assert resp.status_code == 404

    def test_download_rejects_wrong_bearer(self, isolated_app, monkeypatch):
        monkeypatch.setattr("app.main._API_KEY", "test-secret")
        resp = isolated_app.get(
            "/documents/nonexistent/download",
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401


class TestPathTraversalDefense:
    """document_id 검증 — 12자 hex 이외의 값은 404(경로 주입 방지)."""

    def test_download_rejects_non_hex_id(self, isolated_app):
        resp = isolated_app.get("/documents/not-a-hex-id/download")
        assert resp.status_code == 404

    def test_download_rejects_traversal_attempt(self, isolated_app):
        """URL path traversal 시도 — FastAPI는 '/'를 segment로 분리하지만 `..`는 허용됨. 검증기가 거부해야 함."""
        resp = isolated_app.get("/documents/..xxxxxxxxxx/download")
        assert resp.status_code == 404

    def test_download_rejects_uppercase_hex(self, isolated_app):
        """대문자 hex는 new_document_id()가 생성하지 않음 → 거부."""
        resp = isolated_app.get("/documents/ABCDEF012345/download")
        assert resp.status_code == 404

    def test_download_rejects_wrong_length(self, isolated_app):
        resp = isolated_app.get("/documents/abc123/download")  # 6자
        assert resp.status_code == 404

    def test_status_rejects_invalid_id(self, isolated_app):
        """다른 엔드포인트(status)도 같은 검증 적용."""
        resp = isolated_app.get("/documents/invalid!id/status")
        assert resp.status_code == 404


class TestCorsConfig:
    """CORS_ORIGINS 기본값 보안."""

    def test_cors_origins_default_is_empty(self):
        """CORS_ORIGINS 환경변수 미설정 시 빈 리스트 — 모든 origin 거부 (안전한 기본값)."""
        import os
        from app.main import _cors_origins
        # 테스트 환경에서는 환경변수 미설정이 기본
        if not os.getenv("CORS_ORIGINS"):
            assert _cors_origins == []
