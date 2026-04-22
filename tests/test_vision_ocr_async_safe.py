from __future__ import annotations

import asyncio
import time
from pathlib import Path

from app.services.vision_ocr import VisionOcrService


class TestVisionAsyncSafe:
    """extract_from_pdf must work regardless of whether caller is in an event loop."""

    def test_works_in_sync_context(self, tmp_path, monkeypatch):
        service = _make_fake_service(tmp_path, monkeypatch)
        result = service.extract_from_pdf(tmp_path / "doc.pdf")
        assert "page text" in result

    def test_works_inside_running_event_loop(self, tmp_path, monkeypatch):
        """Simulate an async caller (FastAPI without to_thread) — must not raise."""
        service = _make_fake_service(tmp_path, monkeypatch)

        async def _caller() -> str:
            # Sync method invoked from inside a running loop
            return service.extract_from_pdf(tmp_path / "doc.pdf")

        result = asyncio.run(_caller())
        assert "page text" in result


def _make_fake_service(tmp_path: Path, monkeypatch) -> VisionOcrService:
    service = VisionOcrService()
    monkeypatch.setattr(service, "_get_client", lambda: object())

    pages = []
    for i in range(3):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"x")
        pages.append(p)
    monkeypatch.setattr(service, "_render_pdf_pages", lambda _: pages)

    def fake_extract(client, path: Path) -> str:
        time.sleep(0.05)
        return f"page text {path.stem}"

    monkeypatch.setattr(service, "_extract_from_image_path", fake_extract)
    return service
