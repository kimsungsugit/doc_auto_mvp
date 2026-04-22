# Testing

> 인간 열람용 테스트 가이드. Claude Code 세션에는 **자동 로드되지 않음** — `tests/` 편집 시 `tests/CLAUDE.md`가 자동 로드된다. 이 파일은 테스트 작성 onboarding 등 상세 설명이 필요할 때.

## 실행

```bash
# 전체
python -m pytest tests/ -v

# 단일 파일
python -m pytest tests/test_extractor.py -v

# 커버리지
python -m pytest tests/ --cov=app --cov-report=term-missing
```

## 테스트 격리 (`tests/conftest.py`)

모듈 로드 시점에 `RATE_LIMIT_ENABLED=0`을 환경변수에 주입 → 반복 호출 시 429 없음.

`_isolate_env` autouse 픽스처가 매 테스트마다:
- `OPENAI_STRUCTURING_ENABLED` / `OPENAI_API_KEY` / `API_KEY` / `CORS_ORIGINS` 환경변수를 `monkeypatch.delenv`로 제거
- `limiter.reset()`으로 rate limit 카운터 초기화
- `schema.reload_config()` 호출로 유형 설정 캐시 리셋 (시작 + 종료 시)

임시 저장소 디렉토리는 별도의 `isolated_storage` 픽스처가 `tmp_path`로 생성.

## 주요 픽스처

| 픽스처 | 용도 |
|--------|------|
| `isolated_storage` | `tmp_path` 기반 격리된 `StorageService` 인스턴스 |
| `isolated_workflow` | 격리된 `DocumentWorkflowService` — OCR/PDF 서비스는 별도 monkeypatch 필요 |
| `isolated_app` | `app.main.storage` / `app.main.workflow`를 격리 인스턴스로 치환한 FastAPI TestClient |

## 파일별 포커스

- `test_app.py`: FastAPI TestClient, workflow 서비스 monkeypatch 패턴
- `test_extractor.py`: 순수 텍스트 입력으로 추출 로직 단위 테스트
- `test_classifier.py`: 문서 유형 분류기 단위 테스트 (가중치/confidence/혼동 케이스)
- `test_sprint1_features.py`: 재분류/일괄검수/재내보내기 기능 통합 테스트
- `test_storage_index.py`: SQLite 인덱스 성능/동시성 테스트
- `test_exporter*.py`: 엑셀 서식/수식/감사 시트 검증
- `test_readiness_endpoint_returns_preflight_status`: `samples/` 디렉토리 상태에 의존

## 모킹 가이드

- **OCR/PDF**: `monkeypatch.setattr(workflow.pdf_text_service, "extract", lambda _: TextExtractionResult(text="...", requires_ocr=False, warnings=[]))`
- **OpenAI API**: 기본값 `OPENAI_STRUCTURING_ENABLED=false`로 규칙 기반 경로만 타도록 함. AI 경로 테스트가 필요하면 `OpenAIStructurer._get_client`를 monkeypatch.
- **Storage 파일 스캔**: `isolated_storage` 사용 — 글로벌 `storage/` 오염 방지

