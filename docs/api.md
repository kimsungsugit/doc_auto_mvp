# API Reference

> 인간 열람용 전체 레퍼런스. Claude Code 세션에는 **자동 로드되지 않음** — 코드 작업 시에는 `app/CLAUDE.md`에 엔드포인트/환경변수 표가 축약되어 자동 로드된다. 이 파일은 API 문서 배포·클라이언트 연동 설명 등에 활용.

## 주요 엔드포인트

| 엔드포인트 | 설명 |
|-----------|------|
| `GET /documents` | 문서 목록 조회 (페이지네이션) |
| `GET /documents/dashboard` | 대시보드 요약 |
| `POST /documents/upload` | 단건 업로드 (rate limit 30/min) |
| `POST /documents/upload/batch` | 다건 업로드 (rate limit 10/min) |
| `POST /documents/{id}/extract` | 동기 추출 (rate limit 20/min) |
| `POST /documents/{id}/extract/async` | 비동기 추출 (BackgroundTasks) |
| `POST /documents/extract/batch` | 배치 추출 |
| `POST /documents/{id}/retry` | 실패 문서 재시도 |
| `POST /documents/{id}/reclassify?document_type=...` | 수동 문서 유형 재분류 |
| `GET /documents/{id}/status` | 진행 상태 조회 (polling용) |
| `GET /documents/{id}/fields` | 필드 조회 |
| `PATCH /documents/{id}/fields` | 필드 수정 (피드백 자동 수집) |
| `POST /documents/{id}/review` | 검수 완료 |
| `POST /documents/review/batch` | 검수 일괄 처리 |
| `POST /documents/{id}/export` | 엑셀 내보내기 (REVIEWED/EXPORTED에서 허용) |
| `GET /documents/{id}/download` | 내보낸 엑셀 파일 다운로드 |
| `GET /documents/{id}/logs` | 처리 로그 + 편집 이력 (API_KEY 필요) |
| `GET /system/document-types` | 지원 문서 유형 목록 |
| `GET /system/ocr-health` | OCR 상태 (Tesseract + Vision API) |
| `GET /system/readiness` | 프리플라이트 점검 결과 |
| `GET /system/ai-accuracy` | AI 추출 정확도 통계 (API_KEY 필요) |
| `GET /system/ai-cache-stats` | AI 응답 캐시 적중률 통계 (API_KEY 필요) |

## BatchReviewResponse 카운트

- `success`: 필수 필드 완비 + 검수 완료된 건수
- `missing_fields`: 필수 필드 누락으로 완전 승인 안 된 건수
- `failed`: 예외로 실패한 건수 (문서 없음 등)
- 불변식: `success + missing_fields + failed == total`

## 환경변수

### OpenAI / AI
| 변수 | 용도 | 기본값 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI API 키 | 필수 (AI 모드 시) |
| `OPENAI_STRUCTURING_ENABLED` | AI 모드 활성화 (`true`) | `false` |
| `OPENAI_STRUCTURING_MODEL` | 텍스트 추출 모델 | `gpt-4o-mini` |
| `OPENAI_VISION_MODEL` | Vision OCR 모델 | `gpt-4o-mini` |
| `OPENAI_TIMEOUT_SECONDS` | API 타임아웃 (초) | `30` |
| `OPENAI_MAX_RETRIES` | SDK 재시도 횟수 | `3` |

### Vision OCR
| 변수 | 용도 | 기본값 |
|------|------|--------|
| `VISION_CONCURRENCY` | 페이지 동시 처리 수 | `4` |
| `VISION_IMAGE_MAX_DIM` | 전송 이미지 최대 변 (px) | `512` |
| `VISION_JPEG_QUALITY` | 전송 JPEG 품질 | `70` |
| `VISION_DETAIL` | Vision `detail` 옵션 (`low`/`high`/`auto`) | `low` |

### AI 캐시
| 변수 | 용도 | 기본값 |
|------|------|--------|
| `AI_CACHE_ENABLED` | 응답 캐시 활성화 | `1` |
| `AI_CACHE_DIR` | 캐시 저장 디렉토리 | `storage/ai_cache` |
| `AI_CACHE_MAX_ENTRIES` | 최대 항목 수 (LRU 방출) | `2000` |

### Tesseract (fallback OCR)
| 변수 | 용도 | 기본값 |
|------|------|--------|
| `TESSERACT_CMD` | Tesseract 실행 경로 | Vision API 없을 때 필수 |
| `GHOSTSCRIPT_CMD` | Ghostscript 실행 경로 | Vision API 없을 때 필수 |
| `TESSDATA_PREFIX` | 언어 데이터 경로 | Vision API 없을 때 필수 |

### 서버 / 보안
| 변수 | 용도 | 기본값 |
|------|------|--------|
| `RATE_LIMIT_ENABLED` | Rate limit 활성화 (`0`이면 비활성) | `1` |
| `MAX_UPLOAD_SIZE` | 업로드 최대 바이트 | `52428800` (50 MB) |
| `API_KEY` | 보호 엔드포인트(`/logs`, `/download`, `/ai-accuracy`, `/ai-cache-stats`) Bearer 토큰 | (비어있으면 무인증 + 부팅 warning) |
| `CORS_ORIGINS` | CORS 허용 도메인 (콤마 구분, `*`로 전체 허용 가능하나 부팅 warning) | (빈 문자열 = 모든 origin 거부) |

### 신뢰도 임계값 (`app/confidence_thresholds.py`)
모듈 import 시점에 평가됨 — 변경하려면 프로세스 재시작 필요.
| 변수 | 용도 | 기본값 |
|------|------|--------|
| `CONFIDENCE_AUTO_APPROVE` | 이 이상이면 자동 승인(`REVIEWED`) | `0.95` |
| `CONFIDENCE_REVIEW_NORMAL` | 이 미만이면 `review_priority=high` | `0.80` |
| `CONFIDENCE_AI_TRUSTED` | AI가 규칙을 이기기 위한 최소 AI conf | `0.85` |
| `CONFIDENCE_AI_MARGIN_OVER_RULE` | AI 승리 조건: AI - 규칙 ≥ 이 값 | `0.10` |
| `CONFIDENCE_RULE_TRUSTED` | 불일치 시 규칙이 이기기 위한 최소 규칙 conf | `0.60` |
| `FIELD_CONFIDENCE_OK` | 이 이상이면 `ValidationStatus.OK`, 미만은 `WARNING` | `0.75` |
