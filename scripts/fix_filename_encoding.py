"""Fix mojibake filenames in existing records.

Windows/구형 클라이언트가 CP949로 업로드한 파일명이 Latin-1로 디코딩돼
`0.Áß½Ä_25.09.04_¿µ¼öÁõ.jpg` 같이 저장된 경우 한글로 복구한다.

원본 바이너리 파일(storage/originals/<id>.jpg)은 document_id 기반 파일명이라
영향 없음 — 오직 JSON의 original_file_name 문자열만 수정.

Usage:
    python scripts/fix_filename_encoding.py --dry-run   # 미리보기
    python scripts/fix_filename_encoding.py             # 실제 적용 + 인덱스 재구성
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows 콘솔(cp949)에서 Latin-1/한글 혼합 출력 시 UnicodeEncodeError 방지.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure:
        _reconfigure(encoding="utf-8", errors="replace")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.models import DocumentRecord  # noqa: E402
from app.services.storage import StorageService  # noqa: E402
from app.settings import DATA_DIR  # noqa: E402


def repair_filename(name: str) -> str:
    """app.main._repair_filename과 동일 로직 — 복구된 이름 또는 원본 유지."""
    if not name or name.isascii():
        return name
    try:
        repaired = name.encode("latin-1").decode("cp949")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return name
    if any("가" <= c <= "힣" for c in repaired):
        return repaired
    return name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="변경 사항만 출력, 파일 저장 안 함")
    args = parser.parse_args()

    storage = StorageService(DATA_DIR)
    repaired_count = 0
    total = 0

    for path in sorted(storage.records_dir.glob("*.json")):
        total += 1
        try:
            record = DocumentRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"[skip] {path.name}: {exc}", file=sys.stderr)
            continue

        old_name = record.original_file_name
        new_name = repair_filename(old_name)
        if old_name == new_name:
            continue

        repaired_count += 1
        print(f"[{'dry' if args.dry_run else 'fix'}] {record.document_id}: {old_name!r} → {new_name!r}")
        if not args.dry_run:
            record.original_file_name = new_name
            storage.save_record(record)

    if args.dry_run:
        print(f"\nDry-run: {repaired_count}/{total} records would be repaired.")
    else:
        # save_record가 JSON + 인덱스 동시 갱신하지만, 혹시 불일치가 있을 수 있으니 마지막에 전체 재구성
        rebuilt = storage.rebuild_index()
        print(f"\nRepaired {repaired_count}/{total} records. Index rebuilt with {rebuilt} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
