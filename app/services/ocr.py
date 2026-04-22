from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


class OcrUnavailableError(RuntimeError):
    pass


class OcrService:
    def __init__(self) -> None:
        self.default_lang = "kor+eng"

    def extract(self, pdf_path: Path) -> str:
        tesseract_command = self._resolve_tesseract_path()
        with tempfile.TemporaryDirectory(prefix="doc-auto-ocr-") as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            image_paths = self._render_pdf_pages(pdf_path, temp_dir)
            page_texts = [self._ocr_image(tesseract_command, image_path) for image_path in image_paths]
        text = "\n".join(item.strip() for item in page_texts if item.strip()).strip()
        if not text:
            raise OcrUnavailableError(f"OCR returned no text for {pdf_path.name}.")
        return text

    def extract_image(self, image_path: Path) -> str:
        tesseract_command = self._resolve_tesseract_path()
        text = self._ocr_image(tesseract_command, image_path).strip()
        if not text:
            raise OcrUnavailableError(f"OCR returned no text for {image_path.name}.")
        return text

    def _resolve_tesseract_path(self) -> str:
        env_value = os.getenv("TESSERACT_CMD")
        candidates: list[str | None] = [
            env_value,
            shutil.which("tesseract"),
        ]
        if sys.platform == "win32":
            candidates += [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]
        else:
            candidates += [
                "/usr/bin/tesseract",
                "/usr/local/bin/tesseract",
            ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return str(candidate)
        raise OcrUnavailableError(
            "OCR engine is not configured. Install Tesseract and set TESSERACT_CMD, "
            "for example: C:\\Program Files\\Tesseract-OCR\\tesseract.exe"
        )

    def _resolve_ghostscript_path(self) -> str:
        env_value = os.getenv("GHOSTSCRIPT_CMD")
        candidates: list[str | None] = [
            env_value,
            shutil.which("gswin64c"),
            shutil.which("gs"),
        ]
        if sys.platform == "win32":
            candidates.append(r"C:\Program Files\gs\gs10.05.1\bin\gswin64c.exe")
        else:
            candidates += [
                "/usr/bin/gs",
                "/usr/local/bin/gs",
            ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return str(candidate)
        raise OcrUnavailableError(
            "PDF rasterizer is not configured. Install Ghostscript or set GHOSTSCRIPT_CMD, "
            "for example: C:\\Program Files\\gs\\gs10.05.1\\bin\\gswin64c.exe"
        )

    def _render_pdf_pages(self, pdf_path: Path, temp_dir: Path) -> list[Path]:
        ghostscript_command = self._resolve_ghostscript_path()
        output_pattern = temp_dir / "page_%03d.png"
        result = subprocess.run(
            [
                ghostscript_command,
                "-dSAFER",
                "-dBATCH",
                "-dNOPAUSE",
                "-sDEVICE=png16m",
                "-r200",
                f"-sOutputFile={output_pattern}",
                str(pdf_path),
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() or "unknown Ghostscript failure"
            raise OcrUnavailableError(f"Ghostscript rendering failed for {pdf_path.name}: {stderr}")
        image_paths = sorted(temp_dir.glob("page_*.png"))
        if not image_paths:
            raise OcrUnavailableError(f"Ghostscript rendered no images for {pdf_path.name}.")
        return image_paths

    def _ocr_image(self, command: str, image_path: Path) -> str:
        env = os.environ.copy()
        tessdata_prefix = self._resolve_tessdata_prefix()
        if tessdata_prefix:
            env["TESSDATA_PREFIX"] = str(tessdata_prefix)
        # Windows 한국어 로케일(cp949)에서 text=True로 두면 Tesseract UTF-8 출력 디코드 실패 →
        # subprocess reader thread가 조용히 터지고 stdout=None이 반환되는 함정. encoding 명시로 방어.
        result = subprocess.run(
            [command, str(image_path), "stdout", "-l", self.default_lang],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() or "unknown OCR failure"
            raise OcrUnavailableError(f"OCR failed for {image_path.name}: {stderr}")
        if result.stdout is None:
            raise OcrUnavailableError(f"OCR returned no output for {image_path.name} (check Tesseract locale/encoding)")
        return result.stdout

    def _resolve_tessdata_prefix(self) -> Path | None:
        env_value = os.getenv("TESSDATA_PREFIX")
        candidates: list[Path | None] = [
            Path(env_value) if env_value else None,
            Path(__file__).resolve().parents[2] / "tools" / "tessdata",
        ]
        if sys.platform == "win32":
            candidates.append(Path(r"C:\Program Files\Tesseract-OCR\tessdata"))
        else:
            candidates += [
                Path("/usr/share/tesseract-ocr/5/tessdata"),
                Path("/usr/share/tessdata"),
            ]
        for candidate in candidates:
            if candidate and candidate.exists():
                return candidate
        return None
