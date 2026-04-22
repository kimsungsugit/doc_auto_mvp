from __future__ import annotations

import time
from pathlib import Path

from app.services.vision_ocr import VisionOcrService


class TestVisionParallel:
    def test_pages_are_processed_concurrently(self, tmp_path, monkeypatch):
        """5 pages × 200ms per page, concurrency 4 — sequential would be 1.0s.

        <0.7s 임계값: 이상적 wall time은 0.4s(배치1: 4병렬 0.2s + 배치2: 1건 0.2s)지만
        Windows 스레드 스케줄러 지터 대응으로 0.3s 여유 확보. 순차 실행(1.0s)과는
        여전히 큰 차이가 있어 병렬성 증명 유지.
        """
        service = VisionOcrService()
        fake_client = object()
        monkeypatch.setattr(service, "_get_client", lambda: fake_client)

        paths = []
        for i in range(5):
            p = tmp_path / f"page_{i}.png"
            p.write_bytes(b"\x89PNG" + bytes(10))
            paths.append(p)

        monkeypatch.setattr(service, "_render_pdf_pages", lambda _: paths)
        monkeypatch.setenv("VISION_CONCURRENCY", "4")

        def fake_extract(client, path: Path) -> str:
            time.sleep(0.2)
            return f"page text {path.stem}"

        monkeypatch.setattr(service, "_extract_from_image_path", fake_extract)

        start = time.perf_counter()
        result = service.extract_from_pdf(tmp_path / "doc.pdf")
        elapsed = time.perf_counter() - start

        assert elapsed < 0.7, f"Expected <0.7s (parallel; sequential=1.0s), got {elapsed:.2f}s"
        assert "page text page_0" in result
        assert "page text page_4" in result

    def test_page_order_preserved(self, tmp_path, monkeypatch):
        service = VisionOcrService()
        monkeypatch.setattr(service, "_get_client", lambda: object())

        paths = [tmp_path / f"page_{i}.png" for i in range(3)]
        for p in paths:
            p.write_bytes(b"x")
        monkeypatch.setattr(service, "_render_pdf_pages", lambda _: paths)

        # Reverse-order delay: first page takes longest
        delays = {paths[0].stem: 0.15, paths[1].stem: 0.05, paths[2].stem: 0.01}

        def fake_extract(client, path: Path) -> str:
            time.sleep(delays[path.stem])
            return f"[{path.stem}]"

        monkeypatch.setattr(service, "_extract_from_image_path", fake_extract)

        result = service.extract_from_pdf(tmp_path / "doc.pdf")
        # Order in result must follow page index, not completion order
        pos0 = result.find("[page_0]")
        pos1 = result.find("[page_1]")
        pos2 = result.find("[page_2]")
        assert 0 <= pos0 < pos1 < pos2
