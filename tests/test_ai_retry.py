from __future__ import annotations

import sys
import types

from app.services.ai_structurer import OpenAIStructurer
from app.services.vision_ocr import VisionOcrService


def _install_fake_openai(monkeypatch, captured: dict) -> None:
    """Install a fake `openai` module that records OpenAI() kwargs."""
    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
    fake_mod = types.ModuleType("openai")
    fake_mod.OpenAI = _FakeClient  # type: ignore[attr-defined]
    fake_mod.APIError = type("APIError", (Exception,), {})  # type: ignore[attr-defined]
    fake_mod.APIConnectionError = type("APIConnectionError", (Exception,), {})  # type: ignore[attr-defined]
    fake_mod.RateLimitError = type("RateLimitError", (Exception,), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_mod)


class TestAiStructurerRetryConfig:
    def test_uses_default_timeout_and_retries(self, monkeypatch):
        captured: dict = {}
        _install_fake_openai(monkeypatch, captured)
        monkeypatch.setenv("OPENAI_STRUCTURING_ENABLED", "1")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_TIMEOUT_SECONDS", raising=False)
        monkeypatch.delenv("OPENAI_MAX_RETRIES", raising=False)

        structurer = OpenAIStructurer()
        client = structurer._get_client()

        assert client is not None
        assert captured["timeout"] == 30.0
        assert captured["max_retries"] == 3

    def test_respects_env_overrides(self, monkeypatch):
        captured: dict = {}
        _install_fake_openai(monkeypatch, captured)
        monkeypatch.setenv("OPENAI_STRUCTURING_ENABLED", "1")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "5.5")
        monkeypatch.setenv("OPENAI_MAX_RETRIES", "7")

        structurer = OpenAIStructurer()
        structurer._get_client()

        assert captured["timeout"] == 5.5
        assert captured["max_retries"] == 7


class TestVisionOcrRetryConfig:
    def test_applies_retry_timeout_config(self, monkeypatch):
        captured: dict = {}
        _install_fake_openai(monkeypatch, captured)
        monkeypatch.setenv("OPENAI_STRUCTURING_ENABLED", "1")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "12")
        monkeypatch.setenv("OPENAI_MAX_RETRIES", "2")

        service = VisionOcrService()
        service._get_client()

        assert captured["timeout"] == 12.0
        assert captured["max_retries"] == 2
