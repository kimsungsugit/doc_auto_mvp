from pathlib import Path

from scripts.export_marketing_pdfs import EXPORT_TARGETS, as_file_uri, build_manifest


def test_as_file_uri_escapes_korean_and_spaces() -> None:
    path = Path(r"C:\Project\데모\doc-auto-mvp\marketing\intro page.html")
    uri = as_file_uri(path)
    assert uri.startswith("file:///C:/Project/")
    assert "intro%20page.html" in uri
    assert "%EB%8D%B0%EB%AA%A8" in uri


def test_build_manifest_includes_file_metadata(tmp_path: Path) -> None:
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"1234")
    second.write_bytes(b"567890")

    manifest = build_manifest([first, second])

    assert manifest["files"][0]["name"] == "a.pdf"
    assert manifest["files"][0]["size_bytes"] == 4
    assert manifest["files"][1]["name"] == "b.pdf"
    assert manifest["files"][1]["size_bytes"] == 6


def test_export_targets_point_to_marketing_html() -> None:
    names = [target.source.name for target in EXPORT_TARGETS]
    assert names == ["intro_page.html", "comparison_page.html", "onepage_summary.html"]
