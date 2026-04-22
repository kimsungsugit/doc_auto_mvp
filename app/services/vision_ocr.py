from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import OpenAI

logger = logging.getLogger(__name__)

try:
    from openai import APIConnectionError, APIError, RateLimitError
    _VISION_API_ERRORS: tuple[type[Exception], ...] = (
        APIError, APIConnectionError, RateLimitError, OSError, RuntimeError, ImportError,
    )
except ImportError:
    _VISION_API_ERRORS = (OSError, RuntimeError, ImportError)


class VisionOcrUnavailableError(RuntimeError):
    pass


class VisionOcrService:
    """OpenAI Vision API based OCR — extracts text from images and scanned PDFs."""

    def __init__(self) -> None:
        self.enabled = os.getenv("OPENAI_STRUCTURING_ENABLED", "").lower() in {"1", "true", "yes"}
        self.model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
        self._client: OpenAI | None = None
        self._client_resolved = False

    @property
    def available(self) -> bool:
        return self._get_client() is not None

    def _get_client(self) -> OpenAI | None:
        if self._client_resolved:
            return self._client
        self._client_resolved = True
        if not self.enabled:
            return None
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=api_key,
                timeout=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30")),
                max_retries=int(os.getenv("OPENAI_MAX_RETRIES", "3")),
            )
        except _VISION_API_ERRORS as exc:
            # ai_structurer와 동일 수준 — 패키지 누락/네트워크/설정 문제 모두 catch해서
            # silent fallback이 아닌 error 로그로 운영자에게 노출.
            logger.error("Vision OCR client 초기화 실패: %s", exc)
            self._client = None
        return self._client

    def extract_from_pdf(self, pdf_path: Path) -> str:
        """Extract text from a scanned PDF. Parallel page processing, safe to call from sync or async context."""
        client = self._get_client()
        if not client:
            raise VisionOcrUnavailableError("Vision OCR is not configured. Set OPENAI_API_KEY and OPENAI_STRUCTURING_ENABLED.")

        image_paths = self._render_pdf_pages(pdf_path)
        if not image_paths:
            raise VisionOcrUnavailableError(f"No pages rendered from {pdf_path.name}.")

        temp_dirs = {p.parent for p in image_paths}
        try:
            page_texts = self._run_pages_sync(client, image_paths)
        finally:
            for temp_dir in temp_dirs:
                shutil.rmtree(temp_dir, ignore_errors=True)

        result = "\n".join(t for t in page_texts if t).strip()
        if not result:
            raise VisionOcrUnavailableError(f"Vision OCR returned no text for {pdf_path.name}.")
        return result

    def _prepare_image_bytes(self, image_path: Path) -> tuple[bytes, str]:
        """리사이즈된 JPEG 바이트와 media_type 반환. 장변 1024px/품질 80 기본."""
        suffix = image_path.suffix.lower()
        default_media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(suffix, "image/png")
        raw = image_path.read_bytes()
        max_dim = int(os.getenv("VISION_IMAGE_MAX_DIM", "512"))
        quality = int(os.getenv("VISION_JPEG_QUALITY", "70"))
        try:
            from io import BytesIO

            from PIL import Image
            with Image.open(BytesIO(raw)) as img:
                img.load()
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                w, h = img.size
                if max(w, h) > max_dim:
                    scale = max_dim / max(w, h)
                    img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                out = buf.getvalue()
                logger.info("Vision image prepared: %dx%d → %.1fKB (q=%d)", img.size[0], img.size[1], len(out) / 1024, quality)
                return out, "image/jpeg"
        except (OSError, ImportError) as exc:
            logger.debug("Image preprocessing skipped (%s): sending original bytes", exc)
            return raw, default_media

    def _run_pages_sync(self, client: Any, image_paths: list[Path]) -> list[str]:
        """Run the async page pipeline safely from any sync context.

        If no event loop is running (typical case — workflow.py calls via asyncio.to_thread),
        just use asyncio.run. If we're already inside a loop (edge case), fall back to a
        private thread so we don't collide with the caller's loop.
        """
        try:
            asyncio.get_running_loop()
            inside_loop = True
        except RuntimeError:
            inside_loop = False

        if not inside_loop:
            return asyncio.run(self._extract_pages_concurrently(client, image_paths))

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                asyncio.run,
                self._extract_pages_concurrently(client, image_paths),
            )
            return future.result()

    async def _extract_pages_concurrently(self, client: Any, image_paths: list[Path]) -> list[str]:
        """Process pages concurrently with a semaphore. Preserves page order in output."""
        concurrency = max(1, int(os.getenv("VISION_CONCURRENCY", "4")))
        semaphore = asyncio.Semaphore(concurrency)

        async def _run(index: int, path: Path) -> tuple[int, str]:
            async with semaphore:
                try:
                    text = await asyncio.to_thread(self._extract_from_image_path, client, path)
                except VisionOcrUnavailableError as exc:
                    logger.warning("Page %d OCR failed: %s", index, exc)
                    return index, ""
                return index, text.strip()

        tasks = [_run(i, p) for i, p in enumerate(image_paths)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        results.sort(key=lambda item: item[0])
        return [text for _, text in results]

    def extract_from_image(self, image_path: Path) -> str:
        """Extract text from an image file using Vision API."""
        client = self._get_client()
        if not client:
            raise VisionOcrUnavailableError("Vision OCR is not configured.")

        text = self._extract_from_image_path(client, image_path)
        if not text.strip():
            raise VisionOcrUnavailableError(f"Vision OCR returned no text for {image_path.name}.")
        return text.strip()

    def _extract_from_image_path(self, client: Any, image_path: Path) -> str:
        """Send a single image to Vision API and extract text.

        이미지가 크면 장변 1600px로 리사이즈하고 JPEG로 재인코딩해 업로드 페이로드를 줄임.
        업로드 시간/타임아웃/비용 절감 + OCR 품질엔 영향 없음.
        """
        image_bytes, media_type = self._prepare_image_bytes(image_path)
        image_data = base64.b64encode(image_bytes).decode("utf-8")

        prompt_text = (
            "이 이미지에서 텍스트를 모두 추출해주세요. "
            "표(테이블)가 있으면 행/열 구조를 유지하여 텍스트로 변환하세요. "
            "숫자, 날짜, 사업자번호 등은 원본 그대로 유지하세요. "
            "추출된 텍스트만 반환하고, 설명이나 요약은 하지 마세요."
        )
        data_url = f"data:{media_type};base64,{image_data}"

        detail_mode = os.getenv("VISION_DETAIL", "low")
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {"type": "image_url", "image_url": {"url": data_url, "detail": detail_mode}},
                        ],
                    }
                ],
            )
        except _VISION_API_ERRORS as error:
            logger.warning("Vision API error type=%s repr=%r payload_kb=%.1f detail=%s", type(error).__name__, error, len(image_data) / 1024, detail_mode)
            raise VisionOcrUnavailableError(f"Vision API call failed ({type(error).__name__}): {error}") from error

        try:
            return response.choices[0].message.content or ""
        except (AttributeError, IndexError):
            return ""

    def _render_pdf_pages(self, pdf_path: Path) -> list[Path]:
        """Render PDF pages to PNG images using pypdf + reportlab or Ghostscript fallback."""
        # Try pypdf-based rendering first (no external dependency)
        try:
            return self._render_with_pypdf(pdf_path)
        except (RuntimeError, ImportError, OSError, KeyError) as exc:
            logger.debug("pypdf image extraction failed, trying Ghostscript: %s", exc)

        # Try Ghostscript as fallback
        try:
            return self._render_with_ghostscript(pdf_path)
        except (RuntimeError, OSError, ImportError) as exc:
            logger.warning("Ghostscript rendering also failed: %s", exc)
            return []

    def _render_with_pypdf(self, pdf_path: Path) -> list[Path]:
        """Extract page images embedded in PDF using pypdf."""
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        image_paths: list[Path] = []
        temp_dir = Path(tempfile.mkdtemp(prefix="vision-ocr-"))

        for page_num, page in enumerate(reader.pages):
            for image_key in page.images:
                image_data = image_key.data
                output_path = temp_dir / f"page_{page_num:03d}.png"
                output_path.write_bytes(image_data)
                image_paths.append(output_path)
                break  # One image per page is sufficient

        if not image_paths:
            raise RuntimeError("No embedded images found in PDF.")
        return image_paths

    def _render_with_ghostscript(self, pdf_path: Path) -> list[Path]:
        """Render PDF pages to PNG using Ghostscript."""
        import shutil
        import subprocess

        gs_candidates: list[str | None] = [
            os.getenv("GHOSTSCRIPT_CMD"),
            shutil.which("gswin64c"),
            shutil.which("gs"),
        ]
        if sys.platform == "win32":
            gs_candidates.append(r"C:\Program Files\gs\gs10.05.1\bin\gswin64c.exe")
        else:
            gs_candidates += ["/usr/bin/gs", "/usr/local/bin/gs"]
        gs_cmd = next((c for c in gs_candidates if c and Path(c).exists()), None)
        if not gs_cmd:
            raise RuntimeError("Ghostscript not found.")

        temp_dir = Path(tempfile.mkdtemp(prefix="vision-ocr-gs-"))
        output_pattern = temp_dir / "page_%03d.png"

        # Windows 한국어 로케일에서 text=True는 cp949로 디코드 → UTF-8 stderr 디코드 실패 시
        # reader thread 예외로 stdout/stderr가 None이 되는 함정. encoding 명시로 방어.
        result = subprocess.run(
            [
                gs_cmd, "-dSAFER", "-dBATCH", "-dNOPAUSE",
                "-sDEVICE=png16m", "-r150",
                f"-sOutputFile={output_pattern}",
                str(pdf_path),
            ],
            capture_output=True, encoding="utf-8", errors="replace", check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Ghostscript failed: {(result.stderr or '').strip()}")

        return sorted(temp_dir.glob("page_*.png"))
