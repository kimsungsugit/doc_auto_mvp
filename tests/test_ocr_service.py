from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.ocr import OcrService, OcrUnavailableError


def test_resolve_tesseract_path_raises_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("app.services.ocr.Path.exists", lambda _self: False)
    service = OcrService()
    with pytest.raises(OcrUnavailableError):
        service._resolve_tesseract_path()


def test_resolve_tesseract_path_uses_env(monkeypatch, tmp_path) -> None:
    fake_cmd = tmp_path / "tesseract.exe"
    fake_cmd.write_text("", encoding="utf-8")
    monkeypatch.setenv("TESSERACT_CMD", str(fake_cmd))
    service = OcrService()
    assert service._resolve_tesseract_path() == str(fake_cmd)


def test_resolve_tessdata_prefix_prefers_env(monkeypatch, tmp_path) -> None:
    fake_tessdata = tmp_path / "tessdata"
    fake_tessdata.mkdir()
    monkeypatch.setenv("TESSDATA_PREFIX", str(fake_tessdata))
    service = OcrService()
    assert service._resolve_tessdata_prefix() == fake_tessdata
