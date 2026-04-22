from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app, limiter


@pytest.fixture
def rate_limited_client(isolated_storage, isolated_workflow, monkeypatch):
    """TestClient with rate limiter forcibly enabled."""
    monkeypatch.setattr("app.main.storage", isolated_storage)
    monkeypatch.setattr("app.main.workflow", isolated_workflow)
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()
    return TestClient(app)


def _upload_file(client: TestClient) -> int:
    return client.post(
        "/documents/upload",
        files={"file": ("tiny.pdf", io.BytesIO(b"%PDF-1.4 stub"), "application/pdf")},
        data={"uploaded_by": "rate-tester"},
    ).status_code


class TestRateLimit:
    def test_upload_blocks_after_30_requests(self, rate_limited_client):
        """31st upload within a minute should return 429."""
        ok_count = 0
        rate_limited = False
        for _ in range(35):
            status = _upload_file(rate_limited_client)
            if status == 200:
                ok_count += 1
            elif status == 429:
                rate_limited = True
                break
        assert rate_limited, "Expected 429 after exceeding limit"
        assert ok_count <= 30

    def test_rate_limit_disabled_allows_unlimited(self, isolated_storage, isolated_workflow, monkeypatch):
        """With RATE_LIMIT_ENABLED=0 (test default), uploads do not 429."""
        monkeypatch.setattr("app.main.storage", isolated_storage)
        monkeypatch.setattr("app.main.workflow", isolated_workflow)
        monkeypatch.setattr(limiter, "enabled", False)
        limiter.reset()
        client = TestClient(app)

        for _ in range(40):
            assert _upload_file(client) == 200
