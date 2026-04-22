from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
MARKETING_DIR = ROOT / "marketing"
OUTPUT_DIR = ROOT / "output" / "pdf"


@dataclass(frozen=True)
class ExportTarget:
    source: Path
    pdf_name: str


EXPORT_TARGETS = (
    ExportTarget(MARKETING_DIR / "intro_page.html", "doc_auto_intro.pdf"),
    ExportTarget(MARKETING_DIR / "comparison_page.html", "doc_auto_comparison.pdf"),
    ExportTarget(MARKETING_DIR / "onepage_summary.html", "doc_auto_onepage_summary.pdf"),
)


def resolve_edge_path() -> Path:
    candidates = [
        os.getenv("EDGE_PATH"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise FileNotFoundError("Microsoft Edge executable not found.")


def as_file_uri(path: Path) -> str:
    return "file:///" + quote(str(path).replace("\\", "/"), safe="/:._-")


def export_pdf(edge_path: Path, target: ExportTarget, output_dir: Path) -> Path:
    output_path = output_dir / target.pdf_name
    command = [
        str(edge_path),
        "--headless",
        "--disable-gpu",
        "--print-to-pdf=" + str(output_path),
        "--print-to-pdf-no-header",
        as_file_uri(target.source),
    ]
    subprocess.run(command, check=True)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Failed to generate PDF: {output_path}")
    return output_path


def build_manifest(paths: list[Path]) -> dict:
    return {
        "output_dir": str(OUTPUT_DIR),
        "files": [
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": path.stat().st_size,
            }
            for path in paths
        ],
    }


def main() -> None:
    edge_path = resolve_edge_path()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    generated: list[Path] = []
    for target in EXPORT_TARGETS:
        generated.append(export_pdf(edge_path, target, OUTPUT_DIR))

    manifest_path = OUTPUT_DIR / "submission_manifest.json"
    manifest_path.write_text(
        json.dumps(build_manifest(generated), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Generated {len(generated)} PDF files")
    for path in generated:
        print(path)
    print(manifest_path)


if __name__ == "__main__":
    main()
