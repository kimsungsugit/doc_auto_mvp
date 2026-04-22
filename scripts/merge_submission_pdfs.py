from __future__ import annotations

import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.export_marketing_pdfs import OUTPUT_DIR, ExportTarget, export_pdf, resolve_edge_path
from scripts.render_submission_cover import main as render_submission_cover

MARKETING_DIR = ROOT / "marketing"

COVER_TARGET = ExportTarget(MARKETING_DIR / "submission_cover.html", "doc_auto_submission_cover.pdf")
MERGED_NAME = "doc_auto_submission_bundle.pdf"


def merge_pdfs(inputs: list[Path], output_path: Path) -> Path:
    writer = PdfWriter()
    for input_path in inputs:
        reader = PdfReader(str(input_path))
        for page in reader.pages:
            writer.add_page(page)
    with output_path.open("wb") as file:
        writer.write(file)
    return output_path


def build_bundle_manifest(cover_path: Path, source_paths: list[Path], merged_path: Path) -> dict:
    return {
        "bundle": {
            "name": merged_path.name,
            "path": str(merged_path),
            "size_bytes": merged_path.stat().st_size,
            "page_count": len(PdfReader(str(merged_path)).pages),
        },
        "order": [cover_path.name, *[path.name for path in source_paths]],
        "inputs": [
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": path.stat().st_size,
            }
            for path in [cover_path, *source_paths]
        ],
    }


def main() -> None:
    edge_path = resolve_edge_path()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    render_submission_cover()
    cover_path = export_pdf(edge_path, COVER_TARGET, OUTPUT_DIR)
    source_paths = [
        OUTPUT_DIR / "doc_auto_intro.pdf",
        OUTPUT_DIR / "doc_auto_comparison.pdf",
        OUTPUT_DIR / "doc_auto_onepage_summary.pdf",
    ]
    missing = [str(path) for path in source_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing source PDFs. Run scripts/export_marketing_pdfs.py first.\n" + "\n".join(missing)
        )

    merged_path = OUTPUT_DIR / MERGED_NAME
    merge_pdfs([cover_path, *source_paths], merged_path)

    manifest_path = OUTPUT_DIR / "submission_bundle_manifest.json"
    manifest_path.write_text(
        json.dumps(build_bundle_manifest(cover_path, source_paths, merged_path), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(cover_path)
    print(merged_path)
    print(manifest_path)


if __name__ == "__main__":
    main()
