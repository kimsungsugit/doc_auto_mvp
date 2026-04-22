# Doc Auto MVP

세금계산서/거래명세서/영수증 등 한국 비즈니스 문서 PDF를 업로드하면 핵심 필드를 자동 추출하고, 검수 후 엑셀로 내보내는 FastAPI MVP.

## 기술 스택

- Python 3.12+, FastAPI, Pydantic v2, openpyxl, pypdf, reportlab
- OCR: OpenAI Vision API (우선) + Tesseract/Ghostscript (fallback)
- AI 추출: OpenAI Responses API (`OPENAI_STRUCTURING_ENABLED=true`)
- 테스트: pytest + pytest-asyncio + pytest-cov
- Rate limit: slowapi
- DB 없음 — `storage/` 디렉토리에 JSON + SQLite 인덱스(`index.db`)
- 문서 유형 설정: `config/document_types.json` (스키마/레이아웃 1곳 관리)

## 개발 명령어

```bash
# 의존성 설치
python -m pip install -e .[dev]

# 서버 실행
python -m uvicorn app.main:app --reload --port 8000

# 테스트 실행
python -m pytest tests/ -v

# 린트
ruff check app/

# 프리플라이트 점검
python scripts/preflight_check.py
python scripts/ocr_smoke_test.py

# SQLite 인덱스 재생성
python scripts/rebuild_index.py
```

## 코드 컨벤션

- `from __future__ import annotations` 모든 모듈 상단에 사용
- 타입 힌트: `str | None` 스타일 (Union 미사용)
- Pydantic BaseModel 기반 request/response 모델
- 한글 라벨/메시지 사용, 코드(변수/함수명)는 영문
- 금액은 문자열로 처리 (`"100,000"` 형태, 콤마 포함)
- 사업자번호 정규화: `XXX-XX-XXXXX` 형태
- 날짜 정규화: `YYYY-MM-DD` 형태
- 필드 신뢰도(confidence)는 0.0~1.0 float
- extraction_source: `"ai"` / `"rule"` / `"manual"` 로 추출 출처 추적
- 테스트에서 OCR/PDF는 monkeypatch로 모킹

## 컨텍스트 로딩 구조

이 루트 `CLAUDE.md`는 항상 로드. 작업 위치에 따라 추가 지침이 자동 로드됨:
- `app/` 편집 시 → `app/CLAUDE.md` (파이프라인·상태 흐름·API·환경변수)
- `app/services/*` 편집 시 → `app/CLAUDE.md` + `app/services/CLAUDE.md` (분류기 가중치·AI 캐시·exporter 동기 규약·Vision 병렬·SQLite 인덱스)
- `tests/` 편집 시 → `tests/CLAUDE.md` (격리 픽스처·모킹 패턴)
- `/release-check`, `/test`, `/lint`, `/cov`, `/preflight` slash command는 `.claude/commands/`에 등록 — 호출 시만 로드
- 인간 열람용 상세 문서는 `docs/architecture.md`, `docs/api.md`, `docs/testing.md` — 필요 시 Read로 조회
