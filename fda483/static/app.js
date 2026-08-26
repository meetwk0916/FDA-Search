const PAGE_SIZE = 20;
const state = {
  offset: 0,
  total: 0,
  latestIndexed: null,
  searchIndexed: null,
  statusTimer: null,
  latestStatusText: null,
};
const initial = new URLSearchParams(location.search);

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
  if (value) select.add(new Option(value, value, true, true));
}

function renderResult(record) {
  const article = document.createElement("article");
  article.className = "result";

  const top = document.createElement("div");
  top.className = "result-top";
  const heading = document.createElement("h3");
  const titleLink = document.createElement("a");
  titleLink.href = record.download_url;
  titleLink.target = "_blank";
  titleLink.rel = "noopener";
  titleLink.textContent =
    record.company || `FDA ${record.record_type || "record"} #${record.media_id}`;
  heading.append(titleLink);
  const date = document.createElement("span");
  date.className = "record-date";
  date.textContent = `检查日期 ${record.record_date || "未知"}`;
  top.append(heading, date);

  const meta = document.createElement("div");
  meta.className = "meta";
  addMeta(meta, "FEI", record.fei);
  addMeta(meta, "Record type", record.record_type);
  addMeta(meta, "地区", [record.state, record.country].filter(Boolean).join(", "));
  addMeta(meta, "机构类型", record.establishment_type);

  const snippet = document.createElement("p");
  snippet.className = "snippet";
  appendHighlightedText(
    snippet,
    record.snippet || "该记录已建立索引；输入关键词可查看命中的原文上下文。"
  );

  const actions = document.createElement("div");
  actions.className = "result-actions";
  const download = document.createElement("a");
  download.className = "download-link";
  download.href = record.download_url;
  download.target = "_blank";
  download.rel = "noopener";
  download.textContent = "打开 FDA 官方原始文件 ↗";
  const pageCount = document.createElement("span");
  pageCount.className = "page-count";
  pageCount.textContent = record.page_count ? `${record.page_count} 页` : "";
  actions.append(download, pageCount);

  article.append(top, meta, snippet, actions);
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

function syncUrl() {
  const params = new URLSearchParams();
  if (elements.query.value.trim()) params.set("q", elements.query.value.trim());
  if (elements.recordType.value) params.set("record_type", elements.recordType.value);
  if (elements.state.value) params.set("state", elements.state.value);
  if (elements.year.value) params.set("year", elements.year.value);
  if (state.offset) params.set("offset", String(state.offset));
  history.replaceState(null, "", params.size ? `?${params}` : location.pathname);
}

async function runSearch(resetOffset = false) {
  if (resetOffset) state.offset = 0;
  elements.results.setAttribute("aria-busy", "true");
  elements.results.innerHTML = '<div class="loading" role="status">正在检索索引…</div>';
  const params = new URLSearchParams({
    q: elements.query.value.trim(),
    record_type: elements.recordType.value,
    state: elements.state.value,
    year: elements.year.value,
    limit: String(PAGE_SIZE),
    offset: String(state.offset),
  });
  syncUrl();

  try {
    const response = await fetch(`/api/search?${params}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.total = payload.total;
    if (state.latestIndexed !== null) state.searchIndexed = state.latestIndexed;
    elements.refreshResults.hidden = true;
    elements.heading.textContent = elements.query.value.trim() ? "搜索结果" : "全部记录";
    elements.summary.textContent = `找到 ${payload.total.toLocaleString()} 条记录`;
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
    updatePagination();
  } catch (error) {
    elements.summary.textContent = "检索失败";
    elements.results.innerHTML =
      '<div class="error" role="alert">无法读取本地索引，请确认搜索服务正在运行。</div>';
    elements.pagination.hidden = true;
    console.error(error);
  } finally {
    elements.results.setAttribute("aria-busy", "false");
  }
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    const indexed = Number(status.documents?.indexed ?? status.indexed);
    const downloadable = Number(
      status.source?.downloadable_documents ?? status.source_downloadable
    );
    const sync = status.sync || {};
    const progress =
      sync.state === "extracting"
        ? ` · 本轮 ${Number(sync.processed_documents).toLocaleString()} / ${Number(sync.pending_documents).toLocaleString()}`
        : "";
    const phase = {
      discovering: " · 正在扫描 FDA",
      extracting: " · 正在建立索引",
      sleeping: " · 等待下轮同步",
      failed: " · 上轮同步失败",
    }[sync.state] || "";
    state.latestStatusText =
      `本地可搜索 ${indexed.toLocaleString()} 份 · FDA 可下载 ${downloadable.toLocaleString()} 份${phase}${progress}`;
    elements.status.textContent = state.latestStatusText;
    const source = status.source || {};
    elements.status.title =
      `FDA 报告 ${Number(source.reported_rows || 0).toLocaleString()} 行；` +
      `稳定枚举 ${Number(source.enumerated_rows || 0).toLocaleString()} 行；` +
      `不可下载 ${Number(source.unavailable_rows || 0).toLocaleString()} 行；` +
      `重复链接 ${Number(source.duplicate_references || 0).toLocaleString()} 行；` +
      `分页差值 ${Number(source.pagination_gap || 0).toLocaleString()} 行`;
    state.latestIndexed = indexed;
    if (state.searchIndexed === null) {
      state.searchIndexed = indexed;
    } else if (indexed > state.searchIndexed) {
      elements.refreshResults.hidden = false;
    }
  } catch {
    if (state.latestIndexed === null) {
      elements.status.textContent = "索引状态不可用";
    } else {
      elements.status.textContent = `${state.latestStatusText} · 状态暂不可用`;
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

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch(true);
});
elements.recordType.addEventListener("change", () => runSearch(true));
elements.state.addEventListener("change", () => runSearch(true));
elements.year.addEventListener("change", () => runSearch(true));
elements.clear.addEventListener("click", () => {
  elements.recordType.value = "";
  elements.state.value = "";
  elements.year.value = "";
  runSearch(true);
});
elements.refreshResults.addEventListener("click", () => runSearch(true));
elements.previous.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - PAGE_SIZE);
  runSearch();
  scrollTo({ top: document.querySelector(".results-section").offsetTop, behavior: "smooth" });
});
elements.next.addEventListener("click", () => {
  state.offset += PAGE_SIZE;
  runSearch();
  scrollTo({ top: document.querySelector(".results-section").offsetTop, behavior: "smooth" });
});

elements.query.value = initial.get("q") || "";
setInitialFilter(elements.recordType, "record_type");
setInitialFilter(elements.state, "state");
setInitialFilter(elements.year, "year");
state.offset = Number(initial.get("offset")) || 0;
document.addEventListener("visibilitychange", startStatusPolling);
startStatusPolling();
runSearch();
