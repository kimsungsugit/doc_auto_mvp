from __future__ import annotations

import os

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

import pytest
from fastapi.testclient import TestClient

from app.main import app, limiter
from app.schema import reload_config
from app.services.storage import StorageService
from app.services.workflow import DocumentWorkflowService


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Remove env vars that could leak between tests (AI flags, API keys). Reset rate limiter + config cache."""
    for var in ("OPENAI_STRUCTURING_ENABLED", "OPENAI_API_KEY", "API_KEY", "CORS_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    # app.main._API_KEY는 모듈 import 시점에 os.getenv로 캐싱됨 → delenv만으론 리셋 안 됨.
    # .env에 API_KEY가 있어도 테스트에서는 보호 엔드포인트를 비활성화 상태로 돌려야 함.
    monkeypatch.setattr("app.main._API_KEY", "")
    limiter.reset()
    # Reset config cache so tests that monkeypatched CONFIG_PATH don't leak stale schema
    reload_config()
    yield
    reload_config()


@pytest.fixture
def isolated_storage(tmp_path):
    """Create an isolated StorageService backed by a temp directory."""
    return StorageService(tmp_path)


@pytest.fixture
def isolated_workflow(isolated_storage):
    """Create an isolated DocumentWorkflowService."""
    return DocumentWorkflowService(isolated_storage)


@pytest.fixture
def isolated_app(isolated_storage, isolated_workflow, monkeypatch):
    """TestClient with isolated storage/workflow — no cross-test pollution."""
    monkeypatch.setattr("app.main.storage", isolated_storage)
    monkeypatch.setattr("app.main.workflow", isolated_workflow)
    return TestClient(app)
