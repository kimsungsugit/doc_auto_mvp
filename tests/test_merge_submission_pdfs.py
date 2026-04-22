from pathlib import Path

from pypdf import PdfWriter

from scripts.merge_submission_pdfs import build_bundle_manifest, merge_pdfs


def create_pdf(path: Path, page_count: int) -> Path:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as file:
        writer.write(file)
    return path


def test_merge_pdfs_combines_page_counts(tmp_path: Path) -> None:
    first = create_pdf(tmp_path / "first.pdf", 1)
    second = create_pdf(tmp_path / "second.pdf", 2)
    merged = tmp_path / "merged.pdf"

    merge_pdfs([first, second], merged)

    assert merged.exists()
    assert merged.stat().st_size > 0


def test_build_bundle_manifest_tracks_order_and_pages(tmp_path: Path) -> None:
    cover = create_pdf(tmp_path / "cover.pdf", 1)
    intro = create_pdf(tmp_path / "intro.pdf", 2)
    compare = create_pdf(tmp_path / "compare.pdf", 1)
    merged = tmp_path / "bundle.pdf"
    merge_pdfs([cover, intro, compare], merged)

    manifest = build_bundle_manifest(cover, [intro, compare], merged)

    assert manifest["bundle"]["name"] == "bundle.pdf"
    assert manifest["bundle"]["page_count"] == 4
    assert manifest["order"] == ["cover.pdf", "intro.pdf", "compare.pdf"]
