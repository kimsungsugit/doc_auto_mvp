from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DocumentStatus(StrEnum):
    UPLOADED = "Uploaded"
    PROCESSING = "Processing"
    EXTRACTED = "Extracted"
    NEEDS_REVIEW = "Needs Review"
    REVIEWED = "Reviewed"
    EXPORTED = "Exported"
    FAILED = "Failed"


class ValidationStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    MISSING = "missing"


class DocumentField(BaseModel):
    field_name: str
    label: str
    value: str = ""
    confidence: float = 0.0
    validation_status: ValidationStatus = ValidationStatus.MISSING
    source_snippet: str = ""
    required: bool = False
    extraction_source: str = "rule"
    updated_by: str | None = None
    updated_at: datetime | None = None


class InvoiceLineItem(BaseModel):
    line_number: int = 0
    item_name: str = ""
    item_spec: str = ""
    quantity: str = ""
    unit_price: str = ""
    supply_amount: str = ""
    tax_amount: str = ""


class DocumentRecord(BaseModel):
    document_id: str
    original_file_name: str
    original_extension: str = ".pdf"
    uploaded_by: str
    uploaded_at: datetime
    file_size: int
    status: DocumentStatus = DocumentStatus.UPLOADED
    requires_ocr: bool = False
    extracted_at: datetime | None = None
    reviewed_at: datetime | None = None
    exported_at: datetime | None = None
    retry_count: int = 0
    auto_approved: bool = False
    review_priority: str = "normal"
    export_file_name: str | None = None
    last_error: str | None = None
    fields: list[DocumentField] = Field(default_factory=list)
    items: list[InvoiceLineItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def fields_by_name(self) -> dict[str, DocumentField]:
        """field_name → DocumentField 조회용 편의 dict.

        매 호출마다 새로 계산(매번 fresh)하므로 fields 리스트가 바뀌어도 동기 이슈 없음.
        호출 빈도가 극단적으로 높지 않다면 캐시 불필요 (연산 비용 < 캐시 무효화 복잡성).
        """
        return {f.field_name: f for f in self.fields}


class UploadResponse(BaseModel):
    document_id: str
    original_file_name: str
    original_extension: str
    uploaded_at: datetime
    file_size: int
    status: DocumentStatus


class DocumentSummary(BaseModel):
    document_id: str
    original_file_name: str
    original_extension: str
    uploaded_by: str
    uploaded_at: datetime
    status: DocumentStatus
    requires_ocr: bool = False
    export_file_name: str | None = None
    warning_count: int = 0
    item_count: int = 0
    last_error: str | None = None


class PaginatedDocumentsResponse(BaseModel):
    items: list[DocumentSummary]
    total: int
    offset: int
    limit: int


class DashboardResponse(BaseModel):
    total_documents: int
    failed_documents: int
    review_pending_documents: int
    auto_approved_documents: int = 0
    exported_documents: int
    recent_documents: list[DocumentSummary]
    failed_recent_documents: list[DocumentSummary]


class OcrHealthResponse(BaseModel):
    ready: bool
    tesseract_configured: bool
    ghostscript_configured: bool
    vision_api_ready: bool = False
    default_lang: str
    detail: str


class ReadinessResponse(BaseModel):
    ready_for_real_data: bool
    template_ready: bool
    sample_suite_ready: bool
    ocr_ready: bool
    openai_ready: bool
    sample_case_count: int
    generated_pdf_count: int
    checklist: list[str]


class ExtractResponse(BaseModel):
    document_id: str
    extraction_status: DocumentStatus
    document_type: str
    document_schema: str
    requires_ocr: bool
    field_count: int
    item_count: int
    extracted_at: datetime | None
    warnings: list[str]
    last_error: str | None = None


class FieldGroup(BaseModel):
    title: str
    field_names: list[str]


class FieldsResponse(BaseModel):
    document_id: str
    status: DocumentStatus
    document_schema: str
    fields: list[DocumentField]
    field_groups: list[FieldGroup] = Field(default_factory=list)
    warnings: list[str]


class FieldUpdateRequest(BaseModel):
    field_name: str
    value: str
    updated_by: str
    comment: str | None = None


class FieldUpdateResponse(BaseModel):
    document_id: str
    save_status: DocumentStatus
    updated_fields: list[str]
    reviewed_at: datetime | None


class ReviewResponse(BaseModel):
    document_id: str
    review_status: DocumentStatus
    reviewed_at: datetime | None
    missing_required_fields: list[str]


class ExportResponse(BaseModel):
    document_id: str
    export_status: DocumentStatus
    export_file_name: str | None
    export_path: str | None
    exported_at: datetime | None


class BatchError(BaseModel):
    document_id: str
    error: str


class BatchExtractResponse(BaseModel):
    results: list[ExtractResponse]
    errors: list[BatchError]
    total: int
    success: int
    failed: int


class BatchReviewResponse(BaseModel):
    results: list[ReviewResponse]
    errors: list[BatchError]
    total: int
    success: int  # 검수 완료 (누락 필드 없음)
    missing_fields: int  # 누락 필드가 있어 완전 승인 안 된 건수
    failed: int  # 예외로 실패한 건수 (문서 없음 등)


class LogEntry(BaseModel):
    timestamp: datetime
    level: str
    message: str


class EditHistoryEntry(BaseModel):
    timestamp: datetime
    field_name: str
    old_value: str
    new_value: str
    updated_by: str
    comment: str | None = None


class LogsResponse(BaseModel):
    document_id: str
    processing_logs: list[LogEntry]
    edit_history: list[EditHistoryEntry]
    last_status: DocumentStatus
    last_error: str | None = None
