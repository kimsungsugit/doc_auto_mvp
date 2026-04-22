const state = {
  documentId: null,
  page: 0,
  pageSize: 10,
};

const uploadForm = document.getElementById("upload-form");
const uploadStatus = document.getElementById("upload-status");
const extractButton = document.getElementById("extract-button");
const reclassifySelect = document.getElementById("reclassify-select");
const reclassifyButton = document.getElementById("reclassify-button");
const reviewButton = document.getElementById("review-button");
const exportButton = document.getElementById("export-button");
const warningsBox = document.getElementById("warnings");
const downloadArea = document.getElementById("download-area");
const logBox = document.getElementById("log-box");
const documentsBody = document.getElementById("documents-body");
const documentSearch = document.getElementById("document-search");
const statusFilter = document.getElementById("status-filter");
const sortFilter = document.getElementById("sort-filter");
const searchButton = document.getElementById("search-button");
const dashboardCards = document.getElementById("dashboard-cards");
const recentDocuments = document.getElementById("recent-documents");
const failedDocuments = document.getElementById("failed-documents");
const ocrHealthBox = document.getElementById("ocr-health");
const fileInput = document.getElementById("file");
const documentMeta = document.getElementById("document-meta");
const documentOverview = document.getElementById("document-overview");
const reviewGuide = document.getElementById("review-guide");
const fieldGroupSections = document.getElementById("field-group-sections");
const paginationBox = document.getElementById("pagination");

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = fileInput.files;
  if (!files.length) {
    uploadStatus.textContent = "업로드할 파일을 선택하세요.";
    return;
  }

  uploadStatus.textContent = "업로드 중...";
  const formData = new FormData();
  formData.append("uploaded_by", document.getElementById("uploaded_by").value || "demo-user");

  let endpoint = "/documents/upload";
  if (files.length === 1) {
    formData.append("file", files[0]);
  } else {
    endpoint = "/documents/upload/batch";
    for (const file of files) {
      formData.append("files", file);
    }
  }

  const response = await fetch(endpoint, {
    method: "POST",
    body: formData,
  });
  const payload = await response.json();

  if (!response.ok) {
    uploadStatus.textContent = payload.detail || "업로드 실패";
    return;
  }

  if (Array.isArray(payload)) {
    state.documentId = payload[0]?.document_id || null;
    uploadStatus.textContent = `업로드 완료: ${payload.length}건 (${payload.map((item) => item.original_file_name).join(", ")})`;
  } else {
    state.documentId = payload.document_id;
    uploadStatus.textContent = `업로드 완료: ${payload.original_file_name} (${payload.document_id})`;
  }

  extractButton.disabled = !state.documentId;
  reviewButton.disabled = true;
  exportButton.disabled = true;
  fieldGroupSections.innerHTML = "";
  documentMeta.innerHTML = "";
  documentOverview.innerHTML = "";
  reviewGuide.innerHTML = "";
  warningsBox.innerHTML = "";
  downloadArea.textContent = "";
  logBox.textContent = "";
  await loadDocumentList();
  await loadDashboard();

  // 업로드 후 자동 추출 옵션
  const autoExtract = document.getElementById("auto-extract")?.checked;
  if (autoExtract) {
    const uploadedIds = Array.isArray(payload)
      ? payload.map((item) => item.document_id)
      : [payload.document_id];
    if (uploadedIds.length === 1) {
      extractButton.click();
    } else {
      uploadStatus.innerHTML = `<span class="spinner"></span> ${uploadedIds.length}건 자동 추출 중...`;
      try {
        const resp = await fetch("/documents/extract/batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(uploadedIds),
        });
        const batchResult = await resp.json();
        uploadStatus.textContent = `자동 추출 완료: 성공 ${batchResult.success || 0}건, 실패 ${batchResult.failed || 0}건`;
      } catch (err) {
        uploadStatus.textContent = `자동 추출 실패: ${err.message}`;
      }
      await loadDocumentList();
      await loadDashboard();
    }
  }
});

extractButton.addEventListener("click", async () => {
  if (!state.documentId) {
    return;
  }
  extractButton.disabled = true;
  uploadStatus.innerHTML = '<span class="spinner"></span> 추출 처리 중...';

  const response = await fetch(`/documents/${state.documentId}/extract/async`, { method: "POST" });
  const payload = await response.json();
  if (!response.ok) {
    uploadStatus.textContent = payload.detail || "추출 실패";
    extractButton.disabled = false;
    return;
  }

  if (payload.extraction_status === "Processing") {
    await pollExtractionStatus(state.documentId);
  } else {
    onExtractionComplete(payload);
  }
});

async function pollExtractionStatus(documentId) {
  const maxAttempts = 60;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    try {
      const response = await fetch(`/documents/${documentId}/status`);
      const status = await response.json();
      if (status.status === "Processing") {
        uploadStatus.innerHTML = `<span class="spinner"></span> 추출 처리 중... (${attempt + 1}회 확인)`;
        continue;
      }
      // Extraction finished
      const extractResponse = await fetch(`/documents/${documentId}/fields`);
      if (extractResponse.ok) {
        uploadStatus.textContent = `추출 완료 (${status.review_priority === "auto" ? "자동 승인" : status.review_priority === "high" ? "우선 검수" : "검수 대기"})`;
        reviewButton.disabled = false;
        extractButton.disabled = false;
        exportButton.disabled = !(status.status === "Reviewed" || status.status === "Exported");
        await loadFields();
        await loadLogs();
        await loadDocumentList();
        await loadDashboard();
      }
      return;
    } catch (_) {
      continue;
    }
  }
  uploadStatus.textContent = "추출 시간 초과. 문서 목록에서 상태를 확인하세요.";
  extractButton.disabled = false;
}

function onExtractionComplete(payload) {
  reviewButton.disabled = false;
  exportButton.disabled = true;
  extractButton.disabled = false;
  uploadStatus.textContent = `추출 완료: ${payload.document_type || "미분류"} / ${payload.field_count}개 필드 / ${payload.item_count}개 품목`;
  loadFields();
  loadLogs();
  loadDocumentList();
  loadDashboard();
}

reviewButton.addEventListener("click", async () => {
  if (!state.documentId) {
    return;
  }
  const response = await fetch(`/documents/${state.documentId}/review`, { method: "POST" });
  const payload = await response.json();
  if (payload.missing_required_fields?.length) {
    downloadArea.textContent = `검수 완료 불가: ${payload.missing_required_fields.join(", ")}`;
    exportButton.disabled = true;
  } else {
    downloadArea.textContent = "검수 완료 처리됐습니다.";
    exportButton.disabled = false;
  }
  await loadFields();
  await loadLogs();
  await loadDocumentList();
  await loadDashboard();
});

exportButton.addEventListener("click", async () => {
  if (!state.documentId) {
    return;
  }
  const response = await fetch(`/documents/${state.documentId}/export`, { method: "POST" });
  const payload = await response.json();
  if (!response.ok) {
    downloadArea.textContent = payload.detail || "엑셀 생성 실패";
    return;
  }
  downloadArea.innerHTML = `
    <div>엑셀 생성 완료: ${payload.export_file_name}</div>
    <div><a href="/documents/${state.documentId}/download">다운로드</a></div>
  `;
  await loadLogs();
  await loadDocumentList();
  await loadDashboard();
});

// 필터 변경 시 첫 페이지로 리셋 — 페이지 5에서 필터 바꾸면 결과가 없어 빈 목록 보이는 현상 방지
function resetAndReload() {
  state.page = 0;
  return loadDocumentList();
}
searchButton.addEventListener("click", resetAndReload);
statusFilter.addEventListener("change", resetAndReload);
sortFilter.addEventListener("change", resetAndReload);
documentSearch.addEventListener("keydown", (e) => { if (e.key === "Enter") resetAndReload(); });

async function loadDashboard() {
  const ocrResponse = await fetch("/system/ocr-health");
  const ocrPayload = await ocrResponse.json();
  ocrHealthBox.textContent = ocrPayload.ready
    ? `OCR 준비 완료 (${ocrPayload.default_lang})`
    : `OCR 미준비: Tesseract 연결이 필요합니다. 기본 언어는 ${ocrPayload.default_lang}입니다.`;

  const response = await fetch("/documents/dashboard");
  const payload = await response.json();
  dashboardCards.innerHTML = `
    <div class="dashboard-card"><strong>${payload.total_documents}</strong><span>전체 문서</span></div>
    <div class="dashboard-card"><strong>${payload.review_pending_documents}</strong><span>검수 대기</span></div>
    <div class="dashboard-card"><strong>${payload.auto_approved_documents || 0}</strong><span>자동 승인</span></div>
    <div class="dashboard-card"><strong>${payload.failed_documents}</strong><span>실패 문서</span></div>
    <div class="dashboard-card"><strong>${payload.exported_documents}</strong><span>엑셀 완료</span></div>
  `;

  // Vision OCR status
  ocrHealthBox.innerHTML = ocrPayload.ready
    ? `OCR 준비 완료 (${ocrPayload.default_lang})${ocrPayload.vision_api_ready ? " · <strong>Vision API 활성</strong>" : ""}`
    : `OCR 미준비: Tesseract 연결이 필요합니다.${ocrPayload.vision_api_ready ? " (Vision API는 활성)" : ""}`;

  // AI accuracy stats
  try {
    const accuracyResponse = await fetch("/system/ai-accuracy");
    const accuracyPayload = await accuracyResponse.json();
    const aiStatsEl = document.getElementById("ai-accuracy-stats");
    if (aiStatsEl) {
      if (accuracyPayload.total_corrections > 0) {
        const fieldEntries = Object.entries(accuracyPayload.fields || {});
        const topFields = fieldEntries.slice(0, 5).map(([name, stats]) => `${name}: ${stats.corrections}건`).join(", ");
        aiStatsEl.innerHTML = `수정 횟수: <strong>${accuracyPayload.total_corrections}</strong>건 (${topFields})`;
      } else {
        aiStatsEl.textContent = "아직 수집된 피드백이 없습니다.";
      }
    }
  } catch (_) { /* ai-accuracy endpoint optional */ }

  recentDocuments.innerHTML = renderMiniTableRows(payload.recent_documents, "recent");
  failedDocuments.innerHTML = renderMiniTableRows(payload.failed_recent_documents, "failed");
}

function renderMiniTableRows(items, variant) {
  if (!items || !items.length) {
    const cols = variant === "failed" ? 4 : 4;
    return `<tr><td class="mini-empty" colspan="${cols}">표시할 문서가 없습니다.</td></tr>`;
  }
  return items
    .map((item) => {
      const badges = renderStatusBadges(item);
      const thirdCol = variant === "failed"
        ? (item.last_error ? `<span class="error-detail" title="${escapeHtml(item.last_error)}">${escapeHtml(item.last_error.length > 30 ? item.last_error.slice(0, 30) + "…" : item.last_error)}</span>` : "-")
        : String(item.warning_count);
      const retryBtn = item.status === "Failed"
        ? `<button type="button" class="mini-btn summary-retry" data-document-id="${item.document_id}">재처리</button>`
        : "";
      return `
        <tr>
          <td><strong title="${escapeHtml(item.original_file_name)}">${escapeHtml(item.original_file_name)}</strong></td>
          <td>${item.status}${badges}</td>
          <td>${thirdCol}</td>
          <td>
            <button type="button" class="mini-btn summary-open" data-document-id="${item.document_id}">열기</button>
            ${retryBtn}
          </td>
        </tr>
      `;
    })
    .join("");
}

function renderPagination(total) {
  if (!paginationBox) return;
  const totalPages = Math.max(1, Math.ceil(total / state.pageSize));
  if (state.page >= totalPages) state.page = totalPages - 1;
  const current = state.page + 1;
  const start = state.page * state.pageSize + 1;
  const end = Math.min(total, start + state.pageSize - 1);
  const info = total === 0 ? "결과 없음" : `${start}-${end} / ${total}건`;
  paginationBox.innerHTML = `
    <button type="button" class="page-btn" data-action="prev" ${state.page === 0 ? "disabled" : ""}>‹ 이전</button>
    <span class="page-info">${info} (${current}/${totalPages})</span>
    <button type="button" class="page-btn" data-action="next" ${state.page >= totalPages - 1 ? "disabled" : ""}>다음 ›</button>
  `;
}

if (paginationBox) {
  paginationBox.addEventListener("click", async (event) => {
    const btn = event.target.closest(".page-btn");
    if (!btn || btn.disabled) return;
    if (btn.dataset.action === "prev" && state.page > 0) state.page -= 1;
    else if (btn.dataset.action === "next") state.page += 1;
    else return;
    await loadDocumentList();
  });
}

async function populateReclassifyOptions(currentType) {
  if (!reclassifySelect) return;
  // Load options once per session; always update selection to match current doc
  if (reclassifySelect.dataset.loaded !== "1") {
    try {
      const resp = await fetch("/system/document-types");
      const data = await resp.json();
      reclassifySelect.innerHTML = "";
      for (const t of data.types || []) {
        const opt = document.createElement("option");
        opt.value = t;
        opt.textContent = t;
        reclassifySelect.appendChild(opt);
      }
      reclassifySelect.dataset.loaded = "1";
    } catch (_) { /* endpoint optional */ }
  }
  // Always sync selection to the currently viewed document's type
  if (currentType && [...reclassifySelect.options].some((o) => o.value === currentType)) {
    reclassifySelect.value = currentType;
  }
}

async function loadFields() {
  const response = await fetch(`/documents/${state.documentId}/fields`);
  const payload = await response.json();
  const fieldMap = new Map(payload.fields.map((field) => [field.field_name, field]));
  const typeField = fieldMap.get("document_type");
  const documentType = typeField?.value || payload.document_schema || "미분류";

  await populateReclassifyOptions(documentType);
  if (reclassifySelect) reclassifySelect.disabled = false;
  if (reclassifyButton) reclassifyButton.disabled = false;

  // Fetch review priority
  let priorityBadge = "";
  try {
    const statusResponse = await fetch(`/documents/${state.documentId}/status`);
    const statusData = await statusResponse.json();
    if (statusData.auto_approved) {
      priorityBadge = '<span class="priority-badge priority-auto">자동 승인</span>';
    } else if (statusData.review_priority === "high") {
      priorityBadge = '<span class="priority-badge priority-high">우선 검수</span>';
    } else if (statusData.review_priority === "normal") {
      priorityBadge = '<span class="priority-badge priority-normal">일반 검수</span>';
    }
  } catch (_) { /* status endpoint optional */ }

  documentMeta.innerHTML = `
    <span class="meta-pill">문서 유형: ${escapeHtml(documentType)}</span>
    <span class="meta-pill">스키마: ${escapeHtml(payload.document_schema || "기본")}</span>
    <span class="meta-pill">상태: ${escapeHtml(payload.status)}</span>
    ${priorityBadge}
  `;
  renderDocumentOverview(documentType, payload.status, payload.fields, fieldMap);
  renderReviewGuide(documentType, payload.warnings, fieldMap);

  warningsBox.innerHTML = "";
  payload.warnings.forEach((warning) => {
    const div = document.createElement("div");
    div.className = "warning-item";
    div.textContent = warning;
    warningsBox.appendChild(div);
  });

  fieldGroupSections.innerHTML = "";
  renderGroupedSections(payload.fields, payload.field_groups || []);

  document.querySelectorAll(".inline-edit").forEach((form) => {
    form.addEventListener("submit", saveField);
  });
  const canExport = payload.status === "Reviewed" || payload.status === "Exported";
  exportButton.disabled = !canExport;
  exportButton.textContent = payload.status === "Exported" ? "엑셀 재생성" : "엑셀 생성";
}

function renderDocumentOverview(documentType, status, fields, fieldMap) {
  const summaryRows = buildSummaryRows(documentType, fieldMap);
  const completion = calculateCompletion(fields);

  documentOverview.innerHTML = `
    <div class="overview-card overview-hero">
      <div class="overview-label">검수 개요</div>
      <strong>${escapeHtml(documentType)}</strong>
      <span>필수 필드 ${completion.completed}/${completion.total} 확인 · 현재 상태 ${escapeHtml(status)}</span>
    </div>
    <div class="overview-card-grid">
      ${summaryRows
        .map(
          (row) => `
            <article class="overview-card">
              <div class="overview-label">${escapeHtml(row.label)}</div>
              <strong>${escapeHtml(row.value || "-")}</strong>
              <span>${escapeHtml(row.hint)}</span>
            </article>
          `
        )
        .join("")}
    </div>
  `;
}

function renderReviewGuide(documentType, warnings, fieldMap) {
  const checklist = buildChecklist(documentType, warnings, fieldMap);
  reviewGuide.innerHTML = `
    <section class="review-guide-panel">
      <header class="review-guide-header">
        <h3>검수 체크포인트</h3>
        <span>${escapeHtml(documentType)} 기준</span>
      </header>
      <ul class="review-guide-list">
        ${checklist
          .map(
            (item) => `
              <li class="${item.statusClass}">
                <strong>${escapeHtml(item.title)}</strong>
                <span>${escapeHtml(item.body)}</span>
              </li>
            `
          )
          .join("")}
      </ul>
    </section>
  `;
}

async function saveField(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const control = form.querySelector("[name='value']");
  const value = normalizeFieldValue(form.dataset.field, control.value);
  const payload = {
    field_name: form.dataset.field,
    value,
    updated_by: document.getElementById("uploaded_by").value || "demo-user",
    comment: "UI 수정",
  };
  const response = await fetch(`/documents/${state.documentId}/fields`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (response.ok) {
    await loadFields();
    await loadLogs();
    await loadDocumentList();
    await loadDashboard();
  }
}

async function loadLogs() {
  const response = await fetch(`/documents/${state.documentId}/logs`);
  const payload = await response.json();
  const lines = payload.processing_logs.map((item) => `[${item.level}] ${item.message}`);
  if (payload.edit_history.length) {
    lines.push("--- 수정 이력 ---");
    payload.edit_history.forEach((item) => {
      lines.push(`${item.field_name}: ${item.old_value} -> ${item.new_value} (${item.updated_by})`);
    });
  }
  logBox.textContent = lines.join("\n");
}

async function loadDocumentList() {
  const params = new URLSearchParams();
  if (documentSearch.value.trim()) {
    params.set("query", documentSearch.value.trim());
  }
  if (statusFilter.value) {
    params.set("status", statusFilter.value);
  }
  if (sortFilter.value) {
    params.set("sort", sortFilter.value);
  }
  params.set("limit", String(state.pageSize));
  params.set("offset", String(state.page * state.pageSize));
  const response = await fetch(`/documents?${params.toString()}`);
  const payload = await response.json();
  const items = payload.items || payload;
  const total = typeof payload.total === "number" ? payload.total : items.length;
  renderPagination(total);
  documentsBody.innerHTML = "";
  const BATCHABLE_STATUSES = new Set(["Uploaded", "Needs Review", "Failed"]);
  items.forEach((item) => {
    const row = document.createElement("tr");
    const canBatch = BATCHABLE_STATUSES.has(item.status);
    const checkboxTitle = canBatch
      ? "일괄 추출 대상으로 선택"
      : "이미 검수/내보내기가 완료되어 일괄 추출 대상이 아닙니다.";
    row.innerHTML = `
      <td><input type="checkbox" class="batch-check" data-document-id="${item.document_id}" title="${checkboxTitle}" ${canBatch ? "" : "disabled"}></td>
      <td>${item.document_id}</td>
      <td>${escapeHtml(item.original_file_name)}</td>
      <td>${item.original_extension}</td>
      <td>${escapeHtml(item.uploaded_by)}</td>
      <td>${item.status}${renderStatusBadges(item)}</td>
      <td>${item.warning_count}</td>
      <td>${item.item_count}</td>
    `;
    row.addEventListener("click", async (event) => {
      if (event.target.type === "checkbox") return;
      state.documentId = item.document_id;
      uploadStatus.textContent = `선택된 문서: ${item.original_file_name} (${item.document_id})`;
      extractButton.disabled = false;
      reviewButton.disabled = item.status === "Uploaded";
      exportButton.disabled = !(item.status === "Reviewed" || item.status === "Exported");
      await loadFields();
      await loadLogs();
    });
    documentsBody.appendChild(row);
  });

  // Update batch extract button state
  const batchBtn = document.getElementById("batch-extract-button");
  if (batchBtn) {
    batchBtn.onclick = batchExtract;
  }
  const batchReviewBtn = document.getElementById("batch-review-button");
  if (batchReviewBtn) {
    batchReviewBtn.onclick = batchReview;
  }

  // "Select all" header checkbox → toggles all enabled row checkboxes
  const selectAll = document.getElementById("batch-select-all");
  if (selectAll) {
    selectAll.checked = false;
    selectAll.onclick = () => {
      document.querySelectorAll(".batch-check:not(:disabled)").forEach((cb) => {
        cb.checked = selectAll.checked;
      });
    };
  }
}

function renderGroupedSections(fields, fieldGroups) {
  const byName = new Map(fields.map((field) => [field.field_name, field]));
  const rendered = new Set();

  fieldGroups.forEach((group) => {
    const groupFields = group.field_names
      .map((fieldName) => byName.get(fieldName))
      .filter((field) => field && shouldDisplayField(field));

    if (!groupFields.length) {
      return;
    }

    const section = document.createElement("section");
    section.className = "field-section";
    const layout = resolveGroupLayout(groupFields);
    section.classList.add(`field-section--${layout}`);
    section.innerHTML = `
      <header class="field-section-header">
        <h3>${escapeHtml(group.title)}</h3>
        <span>${escapeHtml(describeGroup(group, layout))}</span>
      </header>
      <div class="${getGroupContainerClass(layout)}"></div>
    `;

    const grid = section.querySelector(`.${getGroupContainerClass(layout)}`);
    groupFields.forEach((field) => {
      rendered.add(field.field_name);
      grid.appendChild(buildFieldNode(field, layout));
    });

    fieldGroupSections.appendChild(section);
  });

  const extraFields = fields.filter((field) => !rendered.has(field.field_name) && shouldDisplayField(field));
  if (extraFields.length) {
    const section = document.createElement("section");
    section.className = "field-section";
    section.innerHTML = `
      <header class="field-section-header">
        <h3>추가 필드</h3>
      </header>
      <div class="field-card-grid"></div>
    `;
    const grid = section.querySelector(".field-card-grid");
    extraFields.forEach((field) => grid.appendChild(buildFieldCard(field)));
    fieldGroupSections.appendChild(section);
  }
}

function resolveGroupLayout(fields) {
  const fieldNames = new Set(fields.map((field) => field.field_name));
  if (fieldNames.has("supply_amount") || fieldNames.has("tax_amount") || fieldNames.has("total_amount")) {
    return "amounts";
  }
  if (fieldNames.has("supplier_name") || fieldNames.has("buyer_name")) {
    return "parties";
  }
  return "default";
}

function getGroupContainerClass(layout) {
  if (layout === "amounts") {
    return "metric-grid";
  }
  if (layout === "parties") {
    return "party-grid";
  }
  return "field-card-grid";
}

function describeGroup(group, layout) {
  if (layout === "amounts") {
    return "숫자 일관성을 먼저 확인합니다.";
  }
  if (layout === "parties") {
    return "문서의 양 당사자 또는 발행·수신 주체입니다.";
  }
  if (group.title.includes("계약")) {
    return "계약 문맥에서 핵심값을 확인합니다.";
  }
  if (group.title.includes("견적")) {
    return "견적서 핵심값을 편집합니다.";
  }
  return "필요한 값만 수정하고 저장합니다.";
}

function buildFieldNode(field, layout) {
  if (layout === "amounts") {
    return buildMetricCard(field);
  }
  if (layout === "parties") {
    return buildPartyCard(field);
  }
  return buildFieldCard(field);
}

function buildSummaryRows(documentType, fieldMap) {
  const summaryConfigs = {
    전자세금계산서: [
      ["공급자", "supplier_name", "공급 사업자와 상호를 우선 확인합니다."],
      ["공급받는자", "buyer_name", "거래 상대방 명칭이 맞는지 봅니다."],
      ["합계금액", "total_amount", "공급가액과 세액의 합이 일치해야 합니다."],
      ["품목", "item_name", "품목명이 비어 있으면 원문을 다시 확인합니다."],
    ],
    거래명세서: [
      ["거래처", "buyer_name", "수신 거래처가 맞는지 확인합니다."],
      ["발행사", "supplier_name", "명세서 발행 업체를 확인합니다."],
      ["합계금액", "total_amount", "표 하단 합계와 비교합니다."],
      ["품목코드/품목", "item_name", "대표 품목이나 코드가 맞는지 확인합니다."],
    ],
    외부용역계약서: [
      ["계약 상대방", "buyer_name", "계약서 당사자 을/상대방을 확인합니다."],
      ["발주사", "supplier_name", "갑/발주사 명칭을 확인합니다."],
      ["계약금액", "total_amount", "총 계약금액이 정확한지 확인합니다."],
      ["계약건명", "item_name", "프로젝트명 또는 계약건명이 맞아야 합니다."],
    ],
    개발용역견적서: [
      ["발행 업체", "supplier_name", "견적 발행 업체를 확인합니다."],
      ["수신처", "buyer_name", "수신처 귀하 표기를 확인합니다."],
      ["총 견적금액", "total_amount", "견적 총액 기준으로 검수합니다."],
      ["견적 항목", "item_name", "제목/프로젝트명이 제대로 추출됐는지 봅니다."],
    ],
    일반견적서: [
      ["발행 업체", "supplier_name", "견적 발행 업체를 확인합니다."],
      ["수신처", "buyer_name", "수신처 노이즈가 없는지 확인합니다."],
      ["총 견적금액", "total_amount", "최종 견적가를 우선 확인합니다."],
      ["견적 항목", "item_name", "NOTICE나 품명 블록에서 잡힌 값인지 봅니다."],
    ],
  };

  const fallback = [
    ["작성일자", "issue_date", "문서 기준 날짜를 확인합니다."],
    ["공급자", "supplier_name", "문서 주체를 확인합니다."],
    ["상대방", "buyer_name", "수신처 또는 상대방을 확인합니다."],
    ["합계금액", "total_amount", "대표 금액을 확인합니다."],
  ];

  return (summaryConfigs[documentType] || fallback).map(([label, fieldName, hint]) => ({
    label,
    value: fieldMap.get(fieldName)?.value || "-",
    hint,
  }));
}

function buildChecklist(documentType, warnings, fieldMap) {
  const hasWarnings = warnings.length > 0;
  const amountOk = buildAmountCheck(fieldMap);
  const supplierValue = fieldMap.get("supplier_name")?.value || "";
  const buyerValue = fieldMap.get("buyer_name")?.value || "";
  const itemValue = fieldMap.get("item_name")?.value || "";

  const common = [
    {
      title: "문서 주체 확인",
      body: supplierValue && buyerValue ? `${supplierValue} ↔ ${buyerValue}` : "공급자 또는 상대방 값이 비어 있습니다.",
      statusClass: supplierValue && buyerValue ? "check-ok" : "check-warn",
    },
    {
      title: "금액 일관성 확인",
      body: amountOk.message,
      statusClass: amountOk.ok ? "check-ok" : "check-warn",
    },
    {
      title: "경고 메시지 확인",
      body: hasWarnings ? warnings.join(" / ") : "추가 경고가 없습니다.",
      statusClass: hasWarnings ? "check-warn" : "check-ok",
    },
  ];

  const typeSpecific = {
    전자세금계산서: {
      title: "세금계산서 핵심값 확인",
      body: `사업자번호, 공급가액, 세액, 합계가 모두 채워졌는지 확인합니다.`,
      statusClass:
        fieldMap.get("supplier_biz_no")?.value && fieldMap.get("supply_amount")?.value && fieldMap.get("tax_amount")?.value
          ? "check-ok"
          : "check-warn",
    },
    거래명세서: {
      title: "거래 품목 확인",
      body: itemValue || "품목 또는 코드가 비어 있습니다.",
      statusClass: itemValue ? "check-ok" : "check-warn",
    },
    외부용역계약서: {
      title: "계약명 확인",
      body: itemValue || "계약건명이 비어 있습니다.",
      statusClass: itemValue ? "check-ok" : "check-warn",
    },
    개발용역견적서: {
      title: "견적 제목 확인",
      body: itemValue || "견적 제목/용역 항목이 비어 있습니다.",
      statusClass: itemValue ? "check-ok" : "check-warn",
    },
    일반견적서: {
      title: "수신처 노이즈 확인",
      body: buyerValue || "수신처 값이 비어 있습니다.",
      statusClass: buyerValue ? "check-ok" : "check-warn",
    },
  };

  return [...common, typeSpecific[documentType] || common[0]];
}

function buildAmountCheck(fieldMap) {
  const supplyAmount = parseNumber(fieldMap.get("supply_amount")?.value);
  const taxAmount = parseNumber(fieldMap.get("tax_amount")?.value);
  const totalAmount = parseNumber(fieldMap.get("total_amount")?.value);

  if (supplyAmount == null && totalAmount == null) {
    return { ok: false, message: "금액 필드가 비어 있습니다." };
  }
  if (taxAmount == null || supplyAmount == null || totalAmount == null) {
    return { ok: true, message: "대표 금액은 추출됐지만 일부 금액 필드는 비어 있습니다." };
  }

  const matches = supplyAmount + taxAmount === totalAmount;
  return {
    ok: matches,
    message: matches
      ? `공급가액 ${fieldMap.get("supply_amount")?.value} + 세액 ${fieldMap.get("tax_amount")?.value} = 합계 ${fieldMap.get("total_amount")?.value}`
      : `금액 합이 맞지 않습니다. 공급가액 ${fieldMap.get("supply_amount")?.value}, 세액 ${fieldMap.get("tax_amount")?.value}, 합계 ${fieldMap.get("total_amount")?.value}`,
  };
}

function calculateCompletion(fields) {
  const requiredFields = fields.filter((field) => field.required);
  const completed = requiredFields.filter((field) => field.value).length;
  return {
    completed,
    total: requiredFields.length,
  };
}

function parseNumber(value) {
  if (!value) {
    return null;
  }
  const digits = String(value).replace(/[^\d-]/g, "");
  return digits ? Number(digits) : null;
}

function buildFieldCard(field) {
  const card = document.createElement("article");
  card.className = "field-card";
  const sourceBadge = buildSourceBadge(field.extraction_source);
  card.innerHTML = `
    <div class="field-card-header">
      <strong>${escapeHtml(field.label)}</strong>
      <span class="badge ${field.validation_status}">${field.validation_status}</span>
      ${sourceBadge}
    </div>
    <div class="field-card-value">${escapeHtml(field.value || "-")}</div>
    <div class="field-card-meta">
      <span>신뢰도 ${field.confidence}</span>
      <span>${escapeHtml(field.source_snippet || "원문 근거 없음")}</span>
    </div>
    <form class="inline-edit" data-field="${field.field_name}">
      ${buildEditorControl(field)}
      <button type="submit">저장</button>
    </form>
  `;
  return card;
}

function buildMetricCard(field) {
  const card = document.createElement("article");
  card.className = "metric-card";
  card.innerHTML = `
    <div class="metric-label-row">
      <span class="metric-label">${escapeHtml(field.label)}</span>
      <span class="badge ${field.validation_status}">${field.validation_status}</span>
    </div>
    <div class="metric-value">${escapeHtml(field.value || "-")}</div>
    <div class="metric-hint">${escapeHtml(field.source_snippet || "금액 근거 없음")}</div>
    <form class="inline-edit inline-edit--stack" data-field="${field.field_name}">
      ${buildEditorControl(field)}
      <button type="submit">저장</button>
    </form>
  `;
  return card;
}

function buildPartyCard(field) {
  const card = document.createElement("article");
  card.className = "party-card";
  card.innerHTML = `
    <div class="party-label-row">
      <strong>${escapeHtml(field.label)}</strong>
      <span class="badge ${field.validation_status}">${field.validation_status}</span>
    </div>
    <div class="party-value">${escapeHtml(field.value || "-")}</div>
    <div class="party-meta">${escapeHtml(field.source_snippet || "원문 근거 없음")}</div>
    <form class="inline-edit inline-edit--stack" data-field="${field.field_name}">
      ${buildEditorControl(field)}
      <button type="submit">저장</button>
    </form>
  `;
  return card;
}

function buildEditorControl(field) {
  const value = field.value || "";
  const config = getFieldControlConfig(field.field_name);
  if (config.kind === "textarea") {
    return `<textarea name="value" rows="${config.rows}">${escapeHtml(value)}</textarea>`;
  }

  const inputMode = config.inputMode ? ` inputmode="${config.inputMode}"` : "";
  const placeholder = config.placeholder ? ` placeholder="${escapeAttribute(config.placeholder)}"` : "";
  return `<input name="value" type="${config.type}"${inputMode}${placeholder} value="${escapeAttribute(formatFieldValue(field.field_name, value))}">`;
}

function getFieldControlConfig(fieldName) {
  if (fieldName === "issue_date") {
    return { type: "date" };
  }
  if (fieldName === "supplier_biz_no" || fieldName === "buyer_biz_no") {
    return { type: "text", inputMode: "numeric", placeholder: "123-45-67890" };
  }
  if (fieldName === "supply_amount" || fieldName === "tax_amount" || fieldName === "total_amount" || fieldName === "unit_price") {
    return { type: "text", inputMode: "numeric", placeholder: "숫자만 입력 가능" };
  }
  if (fieldName === "item_name" || fieldName === "remark") {
    return { kind: "textarea", rows: fieldName === "remark" ? 3 : 2 };
  }
  return { type: "text" };
}

function formatFieldValue(fieldName, value) {
  if (!value) {
    return "";
  }
  if (fieldName === "issue_date") {
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})$/);
    return match ? value : "";
  }
  if (fieldName === "supplier_biz_no" || fieldName === "buyer_biz_no") {
    return formatBusinessNumber(value);
  }
  if (fieldName === "supply_amount" || fieldName === "tax_amount" || fieldName === "total_amount" || fieldName === "unit_price") {
    return formatAmount(value);
  }
  return value;
}

function normalizeFieldValue(fieldName, value) {
  const trimmed = String(value).trim();
  if (!trimmed) {
    return "";
  }
  if (fieldName === "supplier_biz_no" || fieldName === "buyer_biz_no") {
    return formatBusinessNumber(trimmed);
  }
  if (fieldName === "supply_amount" || fieldName === "tax_amount" || fieldName === "total_amount" || fieldName === "unit_price") {
    return formatAmount(trimmed);
  }
  return trimmed.replace(/\r\n/g, "\n");
}

function formatBusinessNumber(value) {
  const digits = String(value).replace(/[^\d]/g, "").slice(0, 10);
  if (digits.length <= 3) {
    return digits;
  }
  if (digits.length <= 5) {
    return `${digits.slice(0, 3)}-${digits.slice(3)}`;
  }
  return `${digits.slice(0, 3)}-${digits.slice(3, 5)}-${digits.slice(5)}`;
}

function formatAmount(value) {
  const sign = String(value).trim().startsWith("-") ? "-" : "";
  const digits = String(value).replace(/[^\d]/g, "");
  if (!digits) {
    return "";
  }
  return `${sign}${Number(digits).toLocaleString("ko-KR")}`;
}

function shouldDisplayField(field) {
  return field.required || Boolean(field.value) || field.validation_status === "warning" || field.validation_status === "missing";
}

function renderSummaryItems(items) {
  if (!items.length) {
    return "<div class=\"summary-empty\">표시할 문서가 없습니다.</div>";
  }
  return items
    .map(
      (item) => `
        <div class="summary-item">
          <strong>${escapeHtml(item.original_file_name)}</strong>
          <span>${item.status}${renderStatusBadges(item)} · ${item.original_extension} · 경고 ${item.warning_count} · 품목 ${item.item_count}</span>
          ${item.last_error ? `<div class="error-detail">⚠ ${escapeHtml(item.last_error)}</div>` : ""}
          <div class="summary-actions">
            <button type="button" class="summary-open" data-document-id="${item.document_id}">열기</button>
            ${item.status === "Failed" ? `<button type="button" class="summary-retry" data-document-id="${item.document_id}">재처리</button>` : ""}
          </div>
        </div>
      `
    )
    .join("");
}

function renderStatusBadges(item) {
  // 실패 문서의 last_error가 우선. 그 외 경고가 있으면 yellow soft-warning.
  if (item.last_error) {
    const truncated = item.last_error.length > 40 ? item.last_error.slice(0, 40) + "…" : item.last_error;
    return ` <span class="badge error" title="${escapeHtml(item.last_error)}">에러: ${escapeHtml(truncated)}</span>`;
  }
  if (item.warning_count > 0) {
    return ` <span class="badge soft-warning" title="경고 ${item.warning_count}건">⚠ ${item.warning_count}</span>`;
  }
  return "";
}

document.addEventListener("click", async (event) => {
  const retryTrigger = event.target.closest(".summary-retry");
  if (retryTrigger) {
    state.documentId = retryTrigger.dataset.documentId;
    const response = await fetch(`/documents/${state.documentId}/retry`, { method: "POST" });
    const payload = await response.json();
    uploadStatus.textContent = response.ok
      ? `재처리 완료: ${payload.document_type || "미분류"} / ${payload.field_count}개 필드 / ${payload.item_count}개 품목`
      : payload.detail || "재처리 실패";
    await loadFields();
    await loadLogs();
    await loadDocumentList();
    await loadDashboard();
    return;
  }

  const openTrigger = event.target.closest(".summary-open");
  if (openTrigger) {
    state.documentId = openTrigger.dataset.documentId;
    uploadStatus.textContent = `선택된 문서: ${state.documentId}`;
    extractButton.disabled = false;
    await loadFields();
    await loadLogs();
  }
});

if (reclassifyButton) {
  reclassifyButton.addEventListener("click", async () => {
    if (!state.documentId || !reclassifySelect) return;
    const newType = reclassifySelect.value;
    if (!newType) return;
    const ok = confirm(`문서 유형을 "${newType}"(으)로 변경하고 재추출합니다.\n기존 필드값은 덮어써집니다. 진행하시겠습니까?`);
    if (!ok) return;
    reclassifyButton.disabled = true;
    uploadStatus.innerHTML = `<span class="spinner"></span> ${newType}(으)로 재분류 및 재추출 중...`;
    try {
      const resp = await fetch(
        `/documents/${state.documentId}/reclassify?document_type=${encodeURIComponent(newType)}`,
        { method: "POST" },
      );
      const data = await resp.json();
      if (!resp.ok) {
        uploadStatus.textContent = data.detail || "재분류 실패";
        reclassifyButton.disabled = false;
        return;
      }
      uploadStatus.textContent = `재분류 완료: ${data.document_type} / ${data.field_count}개 필드`;
      await loadFields();
      await loadLogs();
      await loadDocumentList();
      await loadDashboard();
    } catch (err) {
      uploadStatus.textContent = `재분류 실패: ${err.message}`;
    } finally {
      reclassifyButton.disabled = false;
    }
  });
}

async function batchReview() {
  const checkboxes = document.querySelectorAll(".batch-check:checked");
  if (!checkboxes.length) {
    uploadStatus.textContent = "검수할 문서를 선택하세요.";
    return;
  }
  // Filter to only Needs Review status
  const rows = Array.from(checkboxes).map((cb) => cb.closest("tr"));
  const eligible = rows
    .filter((r) => r && r.cells[5]?.textContent === "Needs Review")
    .map((r) => r.cells[1].textContent);
  const skipped = rows.length - eligible.length;
  if (!eligible.length) {
    uploadStatus.textContent = `선택된 ${rows.length}건 중 검수 대기(Needs Review) 상태가 없습니다.`;
    return;
  }

  uploadStatus.innerHTML = `<span class="spinner"></span> ${eligible.length}건 일괄 검수 중... (${skipped}건 상태 불일치로 제외)`;
  try {
    const response = await fetch("/documents/review/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(eligible),
    });
    const payload = await response.json();
    let msg = `일괄 검수 완료: 성공 ${payload.success}건, 필수 누락 ${payload.missing_fields ?? 0}건, 오류 ${payload.failed}건`;
    const missingDetail = (payload.results || [])
      .filter((r) => r.missing_required_fields?.length)
      .map((r) => `${r.document_id}: ${r.missing_required_fields.join(", ")}`);
    if (missingDetail.length) {
      msg += ` / 상세: ${missingDetail.join(" | ")}`;
    }
    if (skipped > 0) msg += ` (선택했지만 제외된 문서 ${skipped}건)`;
    uploadStatus.textContent = msg;
  } catch (err) {
    uploadStatus.textContent = `일괄 검수 실패: ${err.message}`;
  }
  await loadDocumentList();
  await loadDashboard();
}

async function batchExtract() {
  const checkboxes = document.querySelectorAll(".batch-check:checked");
  const ids = Array.from(checkboxes).map((cb) => cb.dataset.documentId);
  if (!ids.length) {
    uploadStatus.textContent = "배치 추출할 문서를 선택하세요.";
    return;
  }

  // Needs Review 재추출 시 기존 수정값 덮어쓰기 경고
  const rows = Array.from(checkboxes).map((cb) => cb.closest("tr"));
  const needsReviewCount = rows.filter((r) => r && r.cells[5]?.textContent === "Needs Review").length;
  if (needsReviewCount > 0) {
    const ok = confirm(
      `검수 대기(Needs Review) 문서 ${needsReviewCount}건이 포함되어 있습니다.\n` +
      `재추출 시 기존 수정값이 덮어써집니다. 계속하시겠습니까?`,
    );
    if (!ok) {
      uploadStatus.textContent = "배치 추출 취소됨.";
      return;
    }
  }

  uploadStatus.innerHTML = `<span class="spinner"></span> ${ids.length}건 배치 추출 중...`;
  try {
    const response = await fetch("/documents/extract/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ids),
    });
    const payload = await response.json();
    const successCount = payload.success || 0;
    const failedCount = payload.failed || 0;
    let msg = `배치 추출 완료: 성공 ${successCount}건`;
    if (failedCount > 0) {
      const errorDetails = (payload.errors || []).map((e) => `${e.document_id}: ${e.error}`).join(", ");
      msg += `, 실패 ${failedCount}건 (${errorDetails})`;
    }
    uploadStatus.textContent = msg;
  } catch (error) {
    uploadStatus.textContent = `배치 추출 실패: ${error.message}`;
  }
  await loadDocumentList();
  await loadDashboard();
}

initializePage();

function buildSourceBadge(source) {
  if (source === "ai") return '<span class="source-badge source-ai">AI</span>';
  if (source === "manual") return '<span class="source-badge source-manual">수동</span>';
  if (source === "rule") return '<span class="source-badge source-rule">규칙</span>';
  return "";
}

function escapeHtml(text) {
  return String(text).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function escapeAttribute(text) {
  return escapeHtml(text).replaceAll('"', "&quot;");
}

async function initializePage() {
  await loadDocumentList();
  await loadDashboard();
  await initializeFromQuery();
}

async function initializeFromQuery() {
  const params = new URLSearchParams(window.location.search);
  const documentId = params.get("document_id");
  if (!documentId) {
    return;
  }

  state.documentId = documentId;
  uploadStatus.textContent = `선택된 문서: ${documentId}`;
  extractButton.disabled = false;

  try {
    await loadFields();
    await loadLogs();
  } catch (error) {
    uploadStatus.textContent = `문서 로드 실패: ${documentId}`;
  }
}
