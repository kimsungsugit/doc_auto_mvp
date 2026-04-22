# app/ — 애플리케이션 코드 작업 가이드

이 파일은 `app/` 내 코드를 편집할 때 자동 로드됨. 루트 `CLAUDE.md`의 기술 스택·코드 컨벤션과 함께 적용.
서비스 레이어 심화 규칙은 `app/services/*.py` 편집 시에만 추가 로드되는 `app/services/CLAUDE.md` 참조.

## 핵심 원칙

- 데이터 모델은 `models.py`의 Pydantic BaseModel 중심. `DocumentField.extraction_source`(`"ai"`/`"rule"`/`"manual"`) 의미 보존.
- 문서 유형 설정은 `config/document_types.json` 1곳에서만 관리. 유형별 전용 필드는 `schema.py`의 `TYPE_SPECIFIC_FIELDS`.
- 금액 문자열(`"1,000,000"` 콤마 포함), 사업자번호 `XXX-XX-XXXXX`, 날짜 `YYYY-MM-DD`.

## 파이프라인 & 상태 흐름

```
Upload → PDF텍스트 → (Vision OCR / Tesseract) → AI분류+추출 → 규칙 교차검증 → 신뢰도 라우팅 → 검수 → 엑셀
```

상태: `Uploaded → Processing → (Needs Review | Reviewed | Failed)` → `Reviewed ↔ Exported`. 상세 상태도는 `docs/architecture.md` 참조.

## 문서 유형 분류 (요약)

3단계: ① 규칙 분류기(`extractor.classify_text`) ② AI 분류기(`ai_structurer.classify_and_extract`) ③ 병합(`workflow._merge_classifications`).
우선순위: `forced_type > 양쪽일치 > 단일 결과 > confidence 기반(AI ≥ 0.85 & margin ≥ 0.1 | 규칙 ≥ 0.6) > 기본값`.
분류기 튜닝·가중치 규약은 `app/services/CLAUDE.md` 참조.

## 신뢰도 라우팅 (`workflow._apply_confidence_routing`)

- avg_confidence ≥ **0.95** + 필수필드 완전 + 금액검증 통과 → `REVIEWED` (auto_approved)
- avg_confidence ≥ **0.80** → `NEEDS_REVIEW` (normal)
- avg_confidence < **0.80** → `NEEDS_REVIEW` + review_priority=high

위 임계값은 `app/confidence_thresholds.py`에 중앙 관리 — `CONFIDENCE_AUTO_APPROVE`, `CONFIDENCE_REVIEW_NORMAL` 환경변수로 오버라이드.

## 지원 문서 유형

전자세금계산서 · 거래명세서 · 외부용역계약서 · 개발용역견적서 · 일반견적서(QUOTATION/見積書) · 영수증(카드매출전표/현금영수증/간이영수증).

새 유형은 `config/document_types.json` + `schema.TYPE_SPECIFIC_FIELDS`(필요 시) 2곳.

## API 엔드포인트

| 엔드포인트 | 설명 |
|-----------|------|
| `GET /documents` | 목록 (페이지네이션) |
| `GET /documents/dashboard` | 대시보드 요약 |
| `POST /documents/upload` | 단건 업로드 (30/min) |
| `POST /documents/upload/batch` | 다건 업로드 (10/min) |
| `POST /documents/{id}/extract` | 동기 추출 (20/min) |
| `POST /documents/{id}/extract/async` | 비동기 추출 (BackgroundTasks) |
| `POST /documents/extract/batch` | 배치 추출 |
| `POST /documents/{id}/retry` | 실패 재시도 |
| `POST /documents/{id}/reclassify?document_type=...` | 수동 재분류 |
| `GET /documents/{id}/status` | 진행 상태 (polling) |
| `GET /documents/{id}/fields` | 필드 조회 |
| `PATCH /documents/{id}/fields` | 필드 수정 (피드백 수집) |
| `POST /documents/{id}/review` | 검수 완료 |
| `POST /documents/review/batch` | 일괄 검수 |
| `POST /documents/{id}/export` | 엑셀 내보내기 (REVIEWED/EXPORTED 허용) |
| `GET /documents/{id}/download` | 엑셀 다운로드 |
| `GET /documents/{id}/logs` | 처리 로그 + 편집 이력 (API_KEY) |
| `GET /system/document-types` | 지원 유형 목록 |
| `GET /system/ocr-health` | OCR 상태 |
| `GET /system/readiness` | 프리플라이트 |
| `GET /system/ai-accuracy` | AI 정확도 통계 (API_KEY) |
| `GET /system/ai-cache-stats` | 캐시 적중률 (API_KEY) |

`BatchReviewResponse` 불변식: `success + missing_fields + failed == total`.

## 환경변수 (자주 쓰는 것만)

| 변수 | 기본값 | 비고 |
|------|--------|------|
| `OPENAI_API_KEY` | — | AI 모드 시 필수 |
| `OPENAI_STRUCTURING_ENABLED` | `false` | `true`로 AI-first 활성화 |
| `OPENAI_STRUCTURING_MODEL` | `gpt-4o-mini` | 텍스트 추출 모델 |
| `OPENAI_VISION_MODEL` | `gpt-4o-mini` | Vision OCR 모델 |
| `OPENAI_TIMEOUT_SECONDS` | `30` | SDK 타임아웃 |
| `OPENAI_MAX_RETRIES` | `3` | SDK 재시도 |
| `VISION_CONCURRENCY` | `4` | Vision 페이지 병렬 |
| `VISION_IMAGE_MAX_DIM` | `512` | Vision 이미지 최대 변 (px) |
| `VISION_DETAIL` | `low` | `low`/`high`/`auto` |
| `AI_CACHE_ENABLED` | `1` | 응답 캐시 |
| `AI_CACHE_MAX_ENTRIES` | `2000` | LRU 방출 기준 |
| `RATE_LIMIT_ENABLED` | `1` | `0`이면 비활성 (테스트용) |
| `MAX_UPLOAD_SIZE` | `52428800` | 50 MB |
| `API_KEY` | (빈 값) | 보호 엔드포인트 토큰 |
| `TESSERACT_CMD` / `GHOSTSCRIPT_CMD` / `TESSDATA_PREFIX` | — | Vision 없을 때 fallback OCR |

더 깊은 런타임 세부(AI 캐시 키, Vision 병렬도, SQLite 인덱스)는 `app/services/CLAUDE.md` 참조.
