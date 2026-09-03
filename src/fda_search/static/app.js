const PAGE_SIZE = 20;
const translations = {
  en: {
    brandHome: "FDA OpenRecords Search home",
    pageDescription: "Search FDA public records across PDFs and scanned documents",
    searchLabel: "Search FDA records",
    searchPlaceholder: "Try: sterility testing, warning letter, or a company name",
    searchButton: "Search",
    filtersLabel: "Search filters",
    recordTypeLabel: "Record type",
    allTypes: "All types",
    locationLabel: "State / location",
    allLocations: "All locations",
    yearLabel: "Record year",
    allYears: "All years",
    clearFilters: "Clear filters",
    previousPage: "Previous",
    nextPage: "Next",
    paginationLabel: "Search result pages",
    footer: "Source: U.S. Food & Drug Administration · Verify results against the original FDA document",
    switchLanguage: "切换到中文",
    switchLanguageText: "中文",
    readingStatus: "Reading index status…",
    loading: "Loading…",
    allRecords: "All records",
    filteredResults: "Filtered results",
    refreshResults: "New records available — refresh results",
    matchingDocuments: "{count} indexed documents match the current criteria",
    indexedDocuments: "{count} FDA documents indexed",
    openDocumentLabel: "{company} (opens the official FDA document in a new tab)",
    locationMeta: "Location",
    establishmentMeta: "Establishment type",
    openDocument: "Open the official FDA document ↗",
    unknown: "Unknown",
    pageCount: "{count} pages",
    pageStatus: "Page {page} of {pages}",
    searching: "Searching the index…",
    noResults: "No matching records found. Try fewer terms or check the spelling.",
    searchFailed: "Search failed",
    searchError: "Unable to read the local index. Confirm that the search service is running.",
    phaseDiscovering: " · scanning FDA",
    phaseExtracting: " · building index",
    phaseSleeping: " · waiting for next sync",
    phaseFailed: " · last sync failed",
    indexProgress: "Index progress {stored} / {downloadable} documents{phase}",
    statusTitle: "{stored} stored: {indexed} fully indexed, {ocr} awaiting OCR, {errors} failed. FDA downloadable target: {downloadable}; {unavailable} additional rows are unavailable.",
    statusUnavailable: "Index status unavailable",
    statusTemporarilyUnavailable: "{status} · status temporarily unavailable",
  },
  "zh-CN": {
    brandHome: "FDA OpenRecords Search 首页",
    pageDescription: "FDA OpenRecords Search：让散落在 PDF 和扫描件里的 FDA 公开记录真正可搜索",
    searchLabel: "搜索 FDA 记录",
    searchPlaceholder: "例如：sterility testing、warning letter、企业名称",
    searchButton: "搜索",
    filtersLabel: "筛选条件",
    recordTypeLabel: "记录类型",
    allTypes: "全部类型",
    locationLabel: "州 / 地区",
    allLocations: "全部地区",
    yearLabel: "记录年份",
    allYears: "全部年份",
    clearFilters: "清除筛选",
    previousPage: "上一页",
    nextPage: "下一页",
    paginationLabel: "搜索结果分页",
    footer: "数据来源 U.S. Food & Drug Administration · 结果以 FDA 官方原始文件为准",
    switchLanguage: "Switch to English",
    switchLanguageText: "English",
    readingStatus: "正在读取索引状态…",
    loading: "正在加载…",
    allRecords: "全部记录",
    filteredResults: "筛选结果",
    refreshResults: "有新记录，刷新结果",
    matchingDocuments: "当前条件匹配 {count} 条已收录文档",
    indexedDocuments: "已收录 {count} 条 FDA 文档",
    openDocumentLabel: "{company}（新窗口打开 FDA 官方文件）",
    locationMeta: "地区",
    establishmentMeta: "机构类型",
    openDocument: "打开 FDA 官方原始文件 ↗",
    unknown: "未知",
    pageCount: "{count} 页",
    pageStatus: "第 {page} / {pages} 页",
    searching: "正在检索索引…",
    noResults: "没有找到匹配记录。请缩短关键词或检查拼写。",
    searchFailed: "检索失败",
    searchError: "无法读取本地索引，请确认搜索服务正在运行。",
    phaseDiscovering: " · 正在扫描 FDA",
    phaseExtracting: " · 正在建立索引",
    phaseSleeping: " · 等待下轮同步",
    phaseFailed: " · 上轮同步失败",
    indexProgress: "索引进度 {stored} / {downloadable} 份{phase}",
    statusTitle: "已收录 {stored} 份：正文完整 {indexed} 份，待补 OCR {ocr} 份，失败 {errors} 份。FDA 可下载目标 {downloadable} 份；另有 {unavailable} 行不可下载。",
    statusUnavailable: "索引状态不可用",
    statusTemporarilyUnavailable: "{status} · 状态暂不可用",
  },
};

function preferredLocale() {
  const requested = new URLSearchParams(location.search).get("lang");
  if (requested in translations) return requested;
  let saved = null;
  try {
    saved = localStorage.getItem("fda-openrecords-locale");
  } catch (error) {
    if (!(error instanceof DOMException)) throw error;
  }
  if (saved in translations) return saved;
  return navigator.language.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}

const state = {
  locale: preferredLocale(),
  offset: 0,
  total: 0,
  results: [],
  resultView: "loading",
  activeCriteria: false,
  latestStored: null,
  searchStored: null,
  statusTimer: null,
  latestStatusText: null,
  statusPayload: null,
  statusUnavailable: false,
  searchController: null,
  searchSeq: 0,
};
const initial = new URLSearchParams(location.search);
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const elements = {
  brand: document.querySelector(".brand"),
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
  languageToggle: document.querySelector("#language-toggle"),
  description: document.querySelector('meta[name="description"]'),
};

function t(key, variables = {}) {
  return Object.entries(variables).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, value),
    translations[state.locale][key]
  );
}

function number(value) {
  return Number(value).toLocaleString(state.locale);
}

function translateStaticContent() {
  document.documentElement.lang = state.locale;
  elements.description.content = t("pageDescription");
  elements.brand.href = `/?lang=${encodeURIComponent(state.locale)}`;
  for (const element of document.querySelectorAll("[data-i18n]")) {
    element.textContent = t(element.dataset.i18n);
  }
  for (const element of document.querySelectorAll("[data-i18n-placeholder]")) {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  }
  for (const element of document.querySelectorAll("[data-i18n-aria-label]")) {
    element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
  }
  elements.languageToggle.textContent = t("switchLanguageText");
  elements.languageToggle.setAttribute("aria-label", t("switchLanguage"));
  elements.languageToggle.lang = state.locale === "en" ? "zh-CN" : "en";
  elements.refreshResults.textContent = t("refreshResults");
  elements.previous.textContent = t("previousPage");
  elements.next.textContent = t("nextPage");
}

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
  name.textContent = `${label}${state.locale === "zh-CN" ? "：" : ": "}`;
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
  if (state.activeCriteria) {
    elements.summary.textContent = t("matchingDocuments", { count: number(state.total) });
    return;
  }
  elements.summary.textContent = t("indexedDocuments", { count: number(state.total) });
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
  titleLink.setAttribute("aria-label", t("openDocumentLabel", { company }));
  heading.append(titleLink);
  main.append(heading);

  const meta = document.createElement("div");
  meta.className = "meta";
  addMeta(meta, "FEI", record.fei);
  addMeta(meta, t("locationMeta"), [record.state, record.country].filter(Boolean).join(", "));
  addMeta(meta, t("establishmentMeta"), record.establishment_type);
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
  download.textContent = t("openDocument");
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
  date.textContent = record.record_date || t("unknown");
  side.append(date);
  if (record.page_count) {
    const pageCount = document.createElement("span");
    pageCount.className = "page-count";
    pageCount.textContent = t("pageCount", { count: number(record.page_count) });
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
  elements.pageStatus.textContent = t("pageStatus", {
    page: number(page),
    pages: number(pages),
  });
}

function syncUrl(mode = "replace") {
  const params = new URLSearchParams({ lang: state.locale });
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
  state.resultView = "loading";
  renderCurrentResults();
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
    state.results = payload.results;
    state.activeCriteria = hasActiveCriteria();
    state.resultView = "results";
    if (state.latestStored !== null) state.searchStored = state.latestStored;
    elements.refreshResults.hidden = true;
    renderCurrentResults();
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
    state.resultView = "error";
    renderCurrentResults();
    console.error(error);
  }
}

function renderCurrentResults() {
  if (state.resultView === "loading") {
    elements.heading.textContent = state.activeCriteria ? t("filteredResults") : t("allRecords");
    elements.summary.textContent = t("loading");
    elements.results.replaceChildren(
      Object.assign(document.createElement("div"), {
        className: "loading",
        role: "status",
        textContent: t("searching"),
      })
    );
    elements.pagination.hidden = true;
    return;
  }
  if (state.resultView === "error") {
    elements.summary.textContent = t("searchFailed");
    elements.results.replaceChildren(
      Object.assign(document.createElement("div"), {
        className: "error",
        role: "alert",
        textContent: t("searchError"),
      })
    );
    elements.pagination.hidden = true;
    return;
  }

  elements.heading.textContent = state.activeCriteria ? t("filteredResults") : t("allRecords");
  updateResultSummary();
  elements.results.replaceChildren(
    ...(state.results.length
      ? state.results.map(renderResult)
      : [Object.assign(document.createElement("div"), {
          className: "empty",
          textContent: t("noResults"),
        })])
  );
  updatePagination();
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
    state.statusPayload = status;
    state.statusUnavailable = false;
    renderStatus();
    const stored = Number(status.documents?.stored ?? status.total);
    state.latestStored = stored;
    if (state.searchStored === null) {
      state.searchStored = stored;
    } else if (stored > state.searchStored) {
      elements.refreshResults.hidden = false;
    }
  } catch (error) {
    state.statusUnavailable = true;
    renderStatus();
    if (!(error instanceof TypeError)) console.error(error);
  }
}

function renderStatus() {
  if (!state.statusPayload) {
    elements.status.textContent = state.statusUnavailable
      ? t("statusUnavailable")
      : t("readingStatus");
    elements.status.removeAttribute("title");
    return;
  }

  const status = state.statusPayload;
  const indexed = Number(status.documents?.indexed ?? status.indexed);
  const stored = Number(status.documents?.stored ?? status.total);
  const ocrRequired = Number(status.documents?.ocr_required ?? status.ocr_required);
  const errors = Number(status.documents?.errors ?? status.errors);
  const downloadable = Number(
    status.source?.downloadable_documents ?? status.source_downloadable
  );
  const phaseKey = {
    discovering: "phaseDiscovering",
    extracting: "phaseExtracting",
    sleeping: "phaseSleeping",
    failed: "phaseFailed",
  }[status.sync?.state];
  const phase = phaseKey ? t(phaseKey) : "";
  state.latestStatusText = t("indexProgress", {
    stored: number(stored),
    downloadable: number(downloadable),
    phase,
  });
  elements.status.textContent = state.statusUnavailable
    ? t("statusTemporarilyUnavailable", { status: state.latestStatusText })
    : state.latestStatusText;
  elements.status.title = t("statusTitle", {
    stored: number(stored),
    indexed: number(indexed),
    ocr: number(ocrRequired),
    errors: number(errors),
    downloadable: number(downloadable),
    unavailable: number(status.source?.unavailable_rows || 0),
  });
}

function setLocale(locale) {
  state.locale = locale;
  try {
    localStorage.setItem("fda-openrecords-locale", locale);
  } catch (error) {
    if (!(error instanceof DOMException)) throw error;
  }
  const url = new URL(location.href);
  url.searchParams.set("lang", locale);
  history.replaceState(null, "", url);
  translateStaticContent();
  renderCurrentResults();
  renderStatus();
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
elements.languageToggle.addEventListener("click", () => {
  setLocale(state.locale === "en" ? "zh-CN" : "en");
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
translateStaticContent();
renderCurrentResults();
renderStatus();
document.addEventListener("visibilitychange", startStatusPolling);
startStatusPolling();
runSearch({ historyMode: "replace" });
