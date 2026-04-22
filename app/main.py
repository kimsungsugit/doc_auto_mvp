from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from app.logging_config import setup_logging
from app.models import (
    BatchError,
    BatchExtractResponse,
    BatchReviewResponse,
    DashboardResponse,
    DocumentStatus,
    ExportResponse,
    ExtractResponse,
    FieldGroup,
    FieldsResponse,
    FieldUpdateRequest,
    FieldUpdateResponse,
    LogsResponse,
    OcrHealthResponse,
    PaginatedDocumentsResponse,
    ReadinessResponse,
    ReviewResponse,
    UploadResponse,
)
from app.schema import get_field_groups, get_supported_document_types, resolve_schema_name
from app.services.ocr import OcrUnavailableError
from app.services.storage import InvalidDocumentIdError, StorageService
from app.services.workflow import DocumentWorkflowService
from app.settings import APP_DIR, BASE_DIR, DATA_DIR

setup_logging(log_dir=DATA_DIR)
SAMPLES_PATH = BASE_DIR / "samples" / "sample_cases.json"
GENERATED_SAMPLES_DIR = BASE_DIR / "samples" / "generated"
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
try:
    MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", str(50 * 1024 * 1024)))
except ValueError:
    MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB default

# 확장자 ↔ 파일 시그니처(매직 바이트) 매핑. 업로드된 파일이 확장자에 해당하는
# 진짜 바이너리인지 검증 — 확장자 스푸핑 방지.
_FILE_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}


def _validate_file_signature(payload: bytes, extension: str) -> bool:
    """True if payload begins with any known signature for `extension`."""
    signatures = _FILE_SIGNATURES.get(extension, ())
    return any(payload.startswith(sig) for sig in signatures)


def _repair_filename(name: str) -> str:
    """CP949 바이트가 Latin-1로 디코딩된 mojibake를 한글로 복구.

    Windows/구형 클라이언트가 multipart Content-Disposition에 CP949로 인코딩된 파일명을
    실을 때, Starlette는 RFC 5987 미존재 시 Latin-1로 해석해 `Áß½Ä` 같은 문자열이 된다.
    실제로 Latin-1 → CP949 round-trip에 성공하고 결과에 한글이 포함될 때만 복구.
    이미 정상 UTF-8 한글이면 encode("latin-1")가 실패하므로 원본 유지.
    """
    if not name or name.isascii():
        return name
    try:
        repaired = name.encode("latin-1").decode("cp949")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return name
    if any("가" <= c <= "힣" for c in repaired):
        return repaired
    return name

_RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1").lower() in {"1", "true", "yes"}
limiter = Limiter(key_func=get_remote_address, enabled=_RATE_LIMIT_ENABLED)


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": f"요청 한도를 초과했습니다: {exc.detail}"},
    )


app = FastAPI(title="문서 자동입력 도우미")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _invalid_doc_id_handler(request: Request, exc: InvalidDocumentIdError) -> JSONResponse:
    """잘못된 document_id(12자 hex 아님) → 404. path traversal 시도도 여기로 떨어짐."""
    return JSONResponse(status_code=404, content={"detail": "문서를 찾을 수 없습니다."})


app.add_exception_handler(InvalidDocumentIdError, _invalid_doc_id_handler)
app.add_middleware(SlowAPIMiddleware)

_logger = logging.getLogger(__name__)

# CORS 설정 — 기본값 빈 문자열(= 아무 origin도 허용 안 함). 개발/배포 시 명시 필수.
_cors_raw = os.getenv("CORS_ORIGINS", "")
_cors_origins = [origin.strip() for origin in _cors_raw.split(",") if origin.strip()]
if "*" in _cors_origins:
    _logger.warning(
        "CORS_ORIGINS='*' 설정 감지 — 모든 도메인이 허용됩니다. "
        "프로덕션 배포라면 특정 도메인으로 제한하세요."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

_API_KEY = os.getenv("API_KEY", "")
if not _API_KEY:
    _logger.warning(
        "API_KEY 미설정 — 보호 엔드포인트(/logs, /download, /ai-accuracy, /ai-cache-stats)가 "
        "인증 없이 열려 있습니다. 프로덕션에서는 반드시 API_KEY를 설정하세요."
    )


def _verify_api_key(authorization: str = Header("", alias="Authorization")) -> None:
    """Verify Bearer token if API_KEY is set. No-op if API_KEY is not configured.

    설정 지침:
    - API_KEY=<랜덤 문자열>로 설정하면 자동 활성화.
    - 클라이언트는 Authorization: Bearer <key> 헤더를 보내야 함.
    - 미설정 시 no-op이라 개발/테스트에서는 편리하지만, 프로덕션에서는 반드시 설정.
    """
    if not _API_KEY:
        return
    if authorization != f"Bearer {_API_KEY}":
        raise HTTPException(status_code=401, detail="인증이 필요합니다.")


storage = StorageService(DATA_DIR)
workflow = DocumentWorkflowService(storage)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except HTTPException:
        # HTTPException은 FastAPI가 처리 — 미들웨어는 재전파만. 개별 엔드포인트에서 이미 로깅됨.
        raise
    except (OSError, TimeoutError) as exc:
        # I/O 계열(디스크 풀, 소켓 타임아웃 등): 구분 로깅 후 재전파.
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        if not request.url.path.startswith("/static"):
            _logger.error(
                "%s %s → 500 I/O error (%.1fms): %s",
                request.method, request.url.path, duration_ms, exc,
            )
        raise
    except Exception as exc:
        # 예상 못 한 예외: 타입 포함해 로깅 (원인 파악 용이).
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        if not request.url.path.startswith("/static"):
            _logger.error(
                "%s %s → 500 %s (%.1fms): %s",
                request.method, request.url.path, type(exc).__name__, duration_ms, exc,
            )
        raise
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    if not request.url.path.startswith("/static"):
        _logger.info("%s %s → %s (%.1fms)", request.method, request.url.path, response.status_code, duration_ms)
    return response


def _build_extract_response(record) -> ExtractResponse:
    document_type = next((field.value for field in record.fields if field.field_name == "document_type"), "")
    return ExtractResponse(
        document_id=record.document_id,
        extraction_status=record.status,
        document_type=document_type,
        document_schema=resolve_schema_name(document_type),
        requires_ocr=record.requires_ocr,
        field_count=len(record.fields),
        item_count=len(record.items),
        extracted_at=record.extracted_at,
        warnings=record.warnings,
        last_error=record.last_error,
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/documents", response_model=PaginatedDocumentsResponse)
async def list_documents(
    query: str = Query("", max_length=500, description="Search by file name, uploader, field values, or document id"),
    status: str = Query("", description="Filter by document status"),
    sort: str = Query("uploaded_at_desc", description="Sort order"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of records"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
) -> PaginatedDocumentsResponse:
    # list_summaries는 SQLite 인덱스만으로 DocumentSummary 생성 — 개별 JSON 로드 없음.
    summaries, total = await asyncio.to_thread(
        storage.list_summaries, query=query, status=status, sort=sort, limit=limit, offset=offset,
    )
    return PaginatedDocumentsResponse(items=summaries, total=total, offset=offset, limit=limit)


@app.get("/documents/dashboard", response_model=DashboardResponse)
async def document_dashboard() -> DashboardResponse:
    def _load() -> DashboardResponse:
        counts = storage.dashboard_counts()
        recent, _ = storage.list_summaries(limit=5)
        failed_recent, _ = storage.list_summaries(status="Failed", limit=5)
        return DashboardResponse(
            total_documents=counts["total"],
            failed_documents=counts["Failed"],
            review_pending_documents=counts["Needs Review"],
            auto_approved_documents=counts["auto_approved"],
            exported_documents=counts["Exported"],
            recent_documents=recent,
            failed_recent_documents=failed_recent,
        )
    return await asyncio.to_thread(_load)


@app.get("/system/ocr-health", response_model=OcrHealthResponse)
async def ocr_health() -> OcrHealthResponse:
    tesseract_ok = False
    ghostscript_ok = False
    detail_parts: list[str] = []

    try:
        workflow.ocr_service._resolve_tesseract_path()
        tesseract_ok = True
    except OcrUnavailableError as error:
        detail_parts.append(str(error))

    try:
        workflow.ocr_service._resolve_ghostscript_path()
        ghostscript_ok = True
    except OcrUnavailableError as error:
        detail_parts.append(str(error))

    vision_ok = workflow.vision_ocr_service.available
    ready = (tesseract_ok and ghostscript_ok) or vision_ok
    return OcrHealthResponse(
        ready=ready,
        tesseract_configured=tesseract_ok,
        ghostscript_configured=ghostscript_ok,
        vision_api_ready=vision_ok,
        default_lang=workflow.ocr_service.default_lang,
        detail=" | ".join(detail_parts) if detail_parts else "OCR is ready.",
    )


@app.get("/system/readiness", response_model=ReadinessResponse)
async def system_readiness() -> ReadinessResponse:
    def _count_samples() -> tuple[int, int]:
        cases = len(json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))) if SAMPLES_PATH.exists() else 0
        pdfs = len(list(GENERATED_SAMPLES_DIR.glob("*.pdf"))) if GENERATED_SAMPLES_DIR.exists() else 0
        return cases, pdfs
    sample_case_count, generated_pdf_count = await asyncio.to_thread(_count_samples)
    ocr = await ocr_health()
    template_ready = await asyncio.to_thread(
        lambda: (BASE_DIR / "templates" / "customer_invoice_template.xlsx").exists()
    )
    sample_suite_ready = sample_case_count >= 19 and generated_pdf_count >= 19
    # env만 보면 openai 패키지가 실제로 설치/초기화되는지 모름 → 실제 client 인스턴스 시도.
    # 시스템 Python과 .venv-ai 혼용 환경에서 패키지 누락으로 silent fallback되는 상황을 탐지.
    def _probe_openai() -> bool:
        from app.services.ai_structurer import OpenAIStructurer
        return OpenAIStructurer()._get_client() is not None

    openai_ready = await asyncio.to_thread(_probe_openai)
    checklist = [
        "비식별 실제 PDF 2~3건 확보",
        "실제 고객 엑셀 양식 1개 확보",
        "스캔 PDF 1~2건 확보",
        "OCR 엔진 설치 상태 확인",
        "OpenAI 구조화 계층 환경변수 확인",
    ]
    ready_for_real_data = template_ready and sample_suite_ready
    return ReadinessResponse(
        ready_for_real_data=ready_for_real_data,
        template_ready=template_ready,
        sample_suite_ready=sample_suite_ready,
        ocr_ready=ocr.ready,
        openai_ready=openai_ready,
        sample_case_count=sample_case_count,
        generated_pdf_count=generated_pdf_count,
        checklist=checklist,
    )


@app.post("/documents/upload", response_model=UploadResponse)
@limiter.limit("30/minute")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    uploaded_by: str = Form("demo-user"),
) -> UploadResponse:
    filename = _repair_filename(file.filename or "")
    extension = Path(filename).suffix.lower()
    if not filename or extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=400, detail="PDF, PNG, JPG, JPEG 파일만 업로드할 수 있습니다.")

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"파일 크기가 제한({MAX_UPLOAD_SIZE // (1024 * 1024)}MB)을 초과합니다.")
    if not _validate_file_signature(payload, extension):
        raise HTTPException(status_code=400, detail="파일 내용이 확장자와 일치하지 않습니다. PDF/PNG/JPEG 원본 파일을 업로드하세요.")
    record = await asyncio.to_thread(storage.create_record, filename, uploaded_by, len(payload))
    await asyncio.to_thread(storage.original_path(record.document_id, record.original_extension).write_bytes, payload)
    return UploadResponse(
        document_id=record.document_id,
        original_file_name=record.original_file_name,
        original_extension=record.original_extension,
        uploaded_at=record.uploaded_at,
        file_size=record.file_size,
        status=record.status,
    )


@app.post("/documents/upload/batch", response_model=list[UploadResponse])
@limiter.limit("10/minute")
async def upload_documents(
    request: Request,
    files: list[UploadFile] = File(...),
    uploaded_by: str = Form("demo-user"),
) -> list[UploadResponse]:
    responses: list[UploadResponse] = []
    for file in files:
        filename = _repair_filename(file.filename or "")
        extension = Path(filename).suffix.lower()
        if not filename or extension not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=400, detail="PDF, PNG, JPG, JPEG 파일만 업로드할 수 있습니다.")
        payload = await file.read()
        if len(payload) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail=f"파일 크기가 제한({MAX_UPLOAD_SIZE // (1024 * 1024)}MB)을 초과합니다.")
        if not _validate_file_signature(payload, extension):
            raise HTTPException(status_code=400, detail="파일 내용이 확장자와 일치하지 않습니다. PDF/PNG/JPEG 원본 파일을 업로드하세요.")
        record = await asyncio.to_thread(storage.create_record, filename, uploaded_by, len(payload))
        await asyncio.to_thread(storage.original_path(record.document_id, record.original_extension).write_bytes, payload)
        responses.append(
            UploadResponse(
                document_id=record.document_id,
                original_file_name=record.original_file_name,
                original_extension=record.original_extension,
                uploaded_at=record.uploaded_at,
                file_size=record.file_size,
                status=record.status,
            )
        )
    return responses


@app.post("/documents/{document_id}/extract", response_model=ExtractResponse)
@limiter.limit("20/minute")
async def extract_document(request: Request, document_id: str) -> ExtractResponse:
    try:
        record = await asyncio.to_thread(workflow.extract, document_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"문서를 찾을 수 없습니다: {document_id}")
    return _build_extract_response(record)


def _run_extract_background(document_id: str) -> None:
    """Background task: run extraction and save result."""
    workflow.extract(document_id)


@app.post("/documents/{document_id}/extract/async", response_model=ExtractResponse)
@limiter.limit("20/minute")
async def extract_document_async(request: Request, document_id: str, background_tasks: BackgroundTasks) -> ExtractResponse:
    try:
        record = await asyncio.to_thread(storage.load_record, document_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"문서를 찾을 수 없습니다: {document_id}")
    record.status = DocumentStatus.PROCESSING
    await asyncio.to_thread(storage.save_record, record)
    await asyncio.to_thread(storage.append_log, document_id, "info", "Async extraction queued")
    background_tasks.add_task(_run_extract_background, document_id)
    return _build_extract_response(record)


@app.get("/system/document-types")
async def supported_document_types() -> dict:
    """List supported document types for manual reclassification UI."""
    return {"types": get_supported_document_types()}


@app.post("/documents/{document_id}/reclassify", response_model=ExtractResponse)
@limiter.limit("20/minute")
async def reclassify_document(request: Request, document_id: str, document_type: str = Query(..., description="새 문서 유형")) -> ExtractResponse:
    """수동 재분류 후 재추출. forced_type으로 분류를 덮어씀."""
    supported = set(get_supported_document_types())
    if document_type not in supported:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 문서 유형: {document_type}")
    try:
        record = await asyncio.to_thread(workflow.extract, document_id, document_type)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"문서를 찾을 수 없습니다: {document_id}")
    return _build_extract_response(record)


@app.post("/documents/{document_id}/retry", response_model=ExtractResponse)
@limiter.limit("20/minute")
async def retry_document(request: Request, document_id: str) -> ExtractResponse:
    try:
        record = await asyncio.to_thread(workflow.extract, document_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"문서를 찾을 수 없습니다: {document_id}")
    return _build_extract_response(record)


@app.post("/documents/extract/batch", response_model=BatchExtractResponse)
@limiter.limit("20/minute")
async def extract_batch(request: Request, document_ids: list[str]) -> BatchExtractResponse:
    results: list[ExtractResponse] = []
    errors: list[BatchError] = []
    for document_id in document_ids:
        try:
            record = await asyncio.to_thread(workflow.extract, document_id)
            results.append(_build_extract_response(record))
        except FileNotFoundError:
            errors.append(BatchError(document_id=document_id, error="문서를 찾을 수 없습니다."))
        except Exception as error:
            _logger.warning("Batch extract failed for %s: %s", document_id, error)
            errors.append(BatchError(document_id=document_id, error=f"{type(error).__name__}: {error}"))
    return BatchExtractResponse(results=results, errors=errors, total=len(document_ids), success=len(results), failed=len(errors))


@app.get("/documents/{document_id}/status")
async def get_document_status(document_id: str) -> dict:
    try:
        record = await asyncio.to_thread(storage.load_record, document_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"문서를 찾을 수 없습니다: {document_id}")
    return {
        "document_id": record.document_id,
        "status": record.status,
        "auto_approved": record.auto_approved,
        "review_priority": record.review_priority,
        "extracted_at": record.extracted_at,
    }


@app.get("/documents/{document_id}/fields", response_model=FieldsResponse)
async def get_fields(document_id: str) -> FieldsResponse:
    try:
        record = await asyncio.to_thread(storage.load_record, document_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"문서를 찾을 수 없습니다: {document_id}")
    document_type = next((field.value for field in record.fields if field.field_name == "document_type"), "")
    return FieldsResponse(
        document_id=record.document_id,
        status=record.status,
        document_schema=resolve_schema_name(document_type),
        fields=record.fields,
        field_groups=[FieldGroup(title=title, field_names=field_names) for title, field_names in get_field_groups(document_type)],
        warnings=record.warnings,
    )


@app.patch("/documents/{document_id}/fields", response_model=FieldUpdateResponse)
@limiter.limit("60/minute")
async def update_field(request: Request, document_id: str, payload: FieldUpdateRequest) -> FieldUpdateResponse:
    try:
        record = await asyncio.to_thread(
            workflow.update_field,
            document_id=document_id,
            field_name=payload.field_name,
            value=payload.value,
            updated_by=payload.updated_by,
            comment=payload.comment,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"문서를 찾을 수 없습니다: {document_id}")
    return FieldUpdateResponse(
        document_id=record.document_id,
        save_status=record.status,
        updated_fields=[payload.field_name],
        reviewed_at=record.reviewed_at,
    )


@app.post("/documents/review/batch", response_model=BatchReviewResponse)
@limiter.limit("10/minute")
async def finalize_review_batch(request: Request, document_ids: list[str]) -> BatchReviewResponse:
    results: list[ReviewResponse] = []
    errors: list[BatchError] = []
    for document_id in document_ids:
        try:
            record, missing = await asyncio.to_thread(workflow.finalize_review, document_id)
        except FileNotFoundError:
            errors.append(BatchError(document_id=document_id, error="문서를 찾을 수 없습니다."))
            continue
        except Exception as error:
            _logger.warning("Batch review failed for %s: %s", document_id, error)
            errors.append(BatchError(document_id=document_id, error=f"{type(error).__name__}: {error}"))
            continue
        results.append(
            ReviewResponse(
                document_id=record.document_id,
                review_status=record.status,
                reviewed_at=record.reviewed_at,
                missing_required_fields=missing,
            )
        )
    success = len([r for r in results if not r.missing_required_fields])
    missing_fields = len([r for r in results if r.missing_required_fields])
    return BatchReviewResponse(
        results=results,
        errors=errors,
        total=len(document_ids),
        success=success,
        missing_fields=missing_fields,
        failed=len(errors),
    )


@app.post("/documents/{document_id}/review", response_model=ReviewResponse)
@limiter.limit("20/minute")
async def finalize_review(request: Request, document_id: str) -> ReviewResponse:
    try:
        record, missing = await asyncio.to_thread(workflow.finalize_review, document_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"문서를 찾을 수 없습니다: {document_id}")
    return ReviewResponse(
        document_id=record.document_id,
        review_status=record.status,
        reviewed_at=record.reviewed_at,
        missing_required_fields=missing,
    )


@app.post("/documents/{document_id}/export", response_model=ExportResponse)
@limiter.limit("20/minute")
async def export_document(request: Request, document_id: str) -> ExportResponse:
    try:
        record = await asyncio.to_thread(workflow.export, document_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return ExportResponse(
        document_id=record.document_id,
        export_status=record.status,
        export_file_name=record.export_file_name,
        export_path=f"/documents/{document_id}/download",
        exported_at=record.exported_at,
    )


@app.get("/documents/{document_id}/logs", response_model=LogsResponse, dependencies=[Depends(_verify_api_key)])
async def get_logs(document_id: str) -> LogsResponse:
    try:
        record = await asyncio.to_thread(storage.load_record, document_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"문서를 찾을 수 없습니다: {document_id}")
    return LogsResponse(
        document_id=record.document_id,
        processing_logs=await asyncio.to_thread(storage.load_logs, document_id),
        edit_history=await asyncio.to_thread(storage.load_audit, document_id),
        last_status=record.status,
        last_error=record.last_error,
    )


@app.get("/documents/{document_id}/download", dependencies=[Depends(_verify_api_key)])
async def download_export(document_id: str) -> FileResponse:
    path = storage.export_path(document_id)
    if not await asyncio.to_thread(path.exists):
        raise HTTPException(status_code=404, detail="엑셀 파일이 아직 생성되지 않았습니다.")
    return FileResponse(
        path=path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


@app.get("/system/ai-accuracy", dependencies=[Depends(_verify_api_key)])
async def ai_accuracy(document_type: str = Query("", description="Filter by document type")) -> dict:
    return await asyncio.to_thread(workflow.feedback_collector.get_accuracy_stats, document_type)


@app.get("/system/ai-cache-stats", dependencies=[Depends(_verify_api_key)])
async def ai_cache_stats() -> dict:
    """AI 응답 캐시 히트율 조회 (비용/성능 관찰용)."""
    from app.services.ai_cache import get_default_cache
    return get_default_cache().stats()


# ── API v1 Router ───────────────────────────────────────────────────
v1 = APIRouter(prefix="/api/v1", tags=["v1"])
v1.add_api_route("/documents", list_documents, methods=["GET"])
v1.add_api_route("/documents/dashboard", document_dashboard, methods=["GET"])
v1.add_api_route("/documents/upload", upload_document, methods=["POST"])
v1.add_api_route("/documents/upload/batch", upload_documents, methods=["POST"])
v1.add_api_route("/documents/extract/batch", extract_batch, methods=["POST"])
v1.add_api_route("/documents/{document_id}/extract", extract_document, methods=["POST"])
v1.add_api_route("/documents/{document_id}/extract/async", extract_document_async, methods=["POST"])
v1.add_api_route("/documents/{document_id}/retry", retry_document, methods=["POST"])
v1.add_api_route("/documents/{document_id}/status", get_document_status, methods=["GET"])
v1.add_api_route("/documents/{document_id}/fields", get_fields, methods=["GET"])
v1.add_api_route("/documents/{document_id}/fields", update_field, methods=["PATCH"])
v1.add_api_route("/documents/{document_id}/review", finalize_review, methods=["POST"])
v1.add_api_route("/documents/{document_id}/export", export_document, methods=["POST"])
v1.add_api_route("/documents/{document_id}/logs", get_logs, methods=["GET"])
v1.add_api_route("/documents/{document_id}/download", download_export, methods=["GET"])
v1.add_api_route("/system/ocr-health", ocr_health, methods=["GET"])
v1.add_api_route("/system/readiness", system_readiness, methods=["GET"])
v1.add_api_route("/system/ai-accuracy", ai_accuracy, methods=["GET"])
app.include_router(v1)
