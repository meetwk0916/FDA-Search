const PAGE_SIZE = 20;
const state = {
  offset: 0,
  total: 0,
  latestStored: null,
  searchStored: null,
  statusTimer: null,
  latestStatusText: null,
  searchController: null,
  searchSeq: 0,
};
const initial = new URLSearchParams(location.search);
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const elements = {
  form: document.querySelector("#search-form"),
  query: document.querySelector("#query"),
  recordType: document.querySelector("#record-type"),
  state: document.querySelector("#state"),
  year: document.querySelector("#year"),
  clear: document.querySelector("#clear-filters"),
  results: document.querySelector("#results"),
  heading: document.querySelector("#results-heading"),
  summary: document.querySelector("#result-summary"),
  status: document.querySelector("#index-status"),
  refreshResults: document.querySelector("#refresh-results"),
  pagination: document.querySelector("#pagination"),
  previous: document.querySelector("#previous-page"),
  next: document.querySelector("#next-page"),
  pageStatus: document.querySelector("#page-status"),
};

function appendHighlightedText(container, text) {
  const parts = text.split(/(<\/?mark>)/i);
  let target = container;
  for (const part of parts) {
    if (part.toLowerCase() === "<mark>") {
      target = document.createElement("mark");
      container.append(target);
    } else if (part.toLowerCase() === "</mark>") {
      target = container;
    } else {
      target.append(document.createTextNode(part));
    }
  }
}

function addMeta(container, label, value) {
  if (!value) return;
  const item = document.createElement("span");
  const name = document.createElement("strong");
  name.textContent = `${label}：`;
  item.append(name, document.createTextNode(value));
  container.append(item);
}

function addFilterOptions(select, values) {
  const selected = select.value;
  const existing = new Set(Array.from(select.options, (option) => option.value));
  for (const value of values) {
    if (!existing.has(value)) select.add(new Option(value, value));
  }
  select.value = selected;
}

function setInitialFilter(select, name) {
  const value = initial.get(name) || "";
  if (!value) return;
  const exists = Array.from(select.options).some((option) => option.value === value);
  if (exists) {
    select.value = value;
  } else {
    select.dataset.pendingValue = value;
  }
}

function applyPendingFilter(select) {
  const value = select.dataset.pendingValue;
  if (!value) return;
  const exists = Array.from(select.options).some((option) => option.value === value);
  if (exists) select.value = value;
  delete select.dataset.pendingValue;
}

function hasActiveCriteria() {
  return Boolean(
    elements.query.value.trim() ||
    elements.recordType.value ||
    elements.state.value ||
    elements.year.value
  );
}

function updateResultSummary() {
  if (hasActiveCriteria()) {
    elements.summary.textContent =
      `当前条件匹配 ${state.total.toLocaleString()} 条已收录文档`;
    return;
  }
  elements.summary.textContent =
    `已收录 ${state.total.toLocaleString()} 条 FDA 文档`;
}

function renderResult(record) {
  const article = document.createElement("article");
  article.className = "result";

  const main = document.createElement("div");
  main.className = "result-main";
  const heading = document.createElement("h3");
  const titleLink = document.createElement("a");
  titleLink.href = record.download_url;
  titleLink.target = "_blank";
  titleLink.rel = "noopener";
  const company = record.company || `FDA ${record.record_type || "record"} #${record.media_id}`;
  titleLink.textContent = `${company} ↗`;
  titleLink.setAttribute("aria-label", `${company}（新窗口打开 FDA 官方文件）`);
  heading.append(titleLink);
  main.append(heading);

  const meta = document.createElement("div");
  meta.className = "meta";
  addMeta(meta, "FEI", record.fei);
  addMeta(meta, "地区", [record.state, record.country].filter(Boolean).join(", "));
  addMeta(meta, "机构类型", record.establishment_type);
  main.append(meta);

  if (record.snippet) {
    const snippet = document.createElement("p");
    snippet.className = "snippet";
    appendHighlightedText(snippet, record.snippet);
    main.append(snippet);
  }

  const actions = document.createElement("div");
  actions.className = "result-actions";
  const download = document.createElement("a");
  download.className = "download-link";
  download.href = record.download_url;
  download.target = "_blank";
  download.rel = "noopener";
  download.textContent = "打开 FDA 官方原始文件 ↗";
  actions.append(download);
  main.append(actions);

  const side = document.createElement("div");
  side.className = "result-side";
  if (record.record_type) {
    const chip = document.createElement("span");
    chip.className = "record-type-chip";
    chip.textContent = record.record_type;
    side.append(chip);
  }
  const date = document.createElement("span");
  date.className = "record-date";
  date.textContent = record.record_date || "未知";
  side.append(date);
  if (record.page_count) {
    const pageCount = document.createElement("span");
    pageCount.className = "page-count";
    pageCount.textContent = `${record.page_count} 页`;
    side.append(pageCount);
  }

  article.append(main, side);
  return article;
}

function updatePagination() {
  const page = Math.floor(state.offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  elements.pagination.hidden = state.total <= PAGE_SIZE;
  elements.previous.disabled = state.offset === 0;
  elements.next.disabled = state.offset + PAGE_SIZE >= state.total;
  elements.pageStatus.textContent = `第 ${page} / ${pages} 页`;
}

function syncUrl(mode = "replace") {
  const params = new URLSearchParams();
  if (elements.query.value.trim()) params.set("q", elements.query.value.trim());
  if (elements.recordType.value) params.set("record_type", elements.recordType.value);
  if (elements.state.value) params.set("state", elements.state.value);
  if (elements.year.value) params.set("year", elements.year.value);
  if (state.offset) params.set("offset", String(state.offset));
  const url = params.size ? `?${params}` : location.pathname;
  if (mode === "push") {
    history.pushState(null, "", url);
  } else {
    history.replaceState(null, "", url);
  }
}

async function runSearch({ resetOffset = false, historyMode = "replace" } = {}) {
  if (state.searchController) state.searchController.abort();
  const controller = new AbortController();
  state.searchController = controller;
  const seq = ++state.searchSeq;

  if (resetOffset) state.offset = 0;
  elements.results.innerHTML = '<div class="loading" role="status">正在检索索引…</div>';
  const params = new URLSearchParams({
    q: elements.query.value.trim(),
    record_type: elements.recordType.value,
    state: elements.state.value,
    year: elements.year.value,
    limit: String(PAGE_SIZE),
    offset: String(state.offset),
  });
  syncUrl(historyMode);

  try {
    const response = await fetch(`/api/search?${params}`, { signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    if (seq !== state.searchSeq) return;
    const payload = await response.json();
    state.total = payload.total;
    if (state.latestStored !== null) state.searchStored = state.latestStored;
    elements.refreshResults.hidden = true;
    elements.heading.textContent = hasActiveCriteria() ? "筛选结果" : "全部记录";
    updateResultSummary();
    elements.results.replaceChildren(
      ...(payload.results.length
        ? payload.results.map(renderResult)
        : [Object.assign(document.createElement("div"), {
            className: "empty",
            textContent: "没有找到匹配记录。请缩短关键词或检查拼写。",
          })])
    );
    addFilterOptions(elements.state, payload.states);
    addFilterOptions(elements.recordType, payload.record_types);
    addFilterOptions(elements.year, payload.years);
    applyPendingFilter(elements.state);
    applyPendingFilter(elements.recordType);
    applyPendingFilter(elements.year);
    updatePagination();
  } catch (error) {
    if (error.name === "AbortError") return;
    if (seq !== state.searchSeq) return;
    elements.summary.textContent = "检索失败";
    elements.results.innerHTML =
      '<div class="error" role="alert">无法读取本地索引，请确认搜索服务正在运行。</div>';
    elements.pagination.hidden = true;
    console.error(error);
  }
}

function applyParamsToControls(params) {
  elements.query.value = params.get("q") || "";
  for (const [select, name] of [
    [elements.recordType, "record_type"],
    [elements.state, "state"],
    [elements.year, "year"],
  ]) {
    const value = params.get(name) || "";
    const exists = Array.from(select.options).some((option) => option.value === value);
    if (value && !exists) {
      select.value = "";
      select.dataset.pendingValue = value;
    } else {
      select.value = value;
      delete select.dataset.pendingValue;
    }
  }
  state.offset = Number(params.get("offset")) || 0;
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    const indexed = Number(status.documents?.indexed ?? status.indexed);
    const stored = Number(status.documents?.stored ?? status.total);
    const ocrRequired = Number(
      status.documents?.ocr_required ?? status.ocr_required
    );
    const errors = Number(status.documents?.errors ?? status.errors);
    const downloadable = Number(
      status.source?.downloadable_documents ?? status.source_downloadable
    );
    const sync = status.sync || {};
    const phase = {
      discovering: " · 正在扫描 FDA",
      extracting: " · 正在建立索引",
      sleeping: " · 等待下轮同步",
      failed: " · 上轮同步失败",
    }[sync.state] || "";
    state.latestStatusText =
      `索引进度 ${stored.toLocaleString()} / ${downloadable.toLocaleString()} 份${phase}`;
    if (elements.status.textContent !== state.latestStatusText) {
      elements.status.textContent = state.latestStatusText;
    }
    const source = status.source || {};
    elements.status.title =
      `已收录 ${stored.toLocaleString()} 份：正文完整 ${indexed.toLocaleString()} 份，` +
      `待补 OCR ${ocrRequired.toLocaleString()} 份，失败 ${errors.toLocaleString()} 份。` +
      `FDA 可下载目标 ${downloadable.toLocaleString()} 份；` +
      `另有 ${Number(source.unavailable_rows || 0).toLocaleString()} 行不可下载。`;
    state.latestStored = stored;
    if (state.searchStored === null) {
      state.searchStored = stored;
    } else if (stored > state.searchStored) {
      elements.refreshResults.hidden = false;
    }
  } catch {
    const fallback = state.latestStored === null
      ? "索引状态不可用"
      : `${state.latestStatusText} · 状态暂不可用`;
    if (elements.status.textContent !== fallback) {
      elements.status.textContent = fallback;
    }
  }
}

function startStatusPolling() {
  if (state.statusTimer !== null) clearInterval(state.statusTimer);
  state.statusTimer = null;
  if (document.hidden) return;
  loadStatus();
  state.statusTimer = setInterval(loadStatus, 30000);
}

function scrollToResults() {
  scrollTo({
    top: document.querySelector(".results-section").offsetTop,
    behavior: reduceMotion.matches ? "instant" : "smooth",
  });
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch({ resetOffset: true, historyMode: "push" });
});
elements.recordType.addEventListener("change", () => runSearch({ resetOffset: true, historyMode: "push" }));
elements.state.addEventListener("change", () => runSearch({ resetOffset: true, historyMode: "push" }));
elements.year.addEventListener("change", () => runSearch({ resetOffset: true, historyMode: "push" }));
elements.clear.addEventListener("click", () => {
  elements.recordType.value = "";
  elements.state.value = "";
  elements.year.value = "";
  runSearch({ resetOffset: true, historyMode: "push" });
});
elements.refreshResults.addEventListener("click", () => runSearch({ resetOffset: true }));
elements.previous.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - PAGE_SIZE);
  runSearch();
  scrollToResults();
});
elements.next.addEventListener("click", () => {
  state.offset += PAGE_SIZE;
  runSearch();
  scrollToResults();
});
window.addEventListener("popstate", () => {
  applyParamsToControls(new URLSearchParams(location.search));
  runSearch();
});

elements.query.value = initial.get("q") || "";
setInitialFilter(elements.recordType, "record_type");
setInitialFilter(elements.state, "state");
setInitialFilter(elements.year, "year");
state.offset = Number(initial.get("offset")) || 0;
document.addEventListener("visibilitychange", startStatusPolling);
startStatusPolling();
runSearch({ historyMode: "replace" });
