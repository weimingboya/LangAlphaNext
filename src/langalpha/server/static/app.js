import {
  buildChartModel,
  initialAgentProjection,
  reduceAgentEvent,
} from "/static/agent-events.mjs";

const state = {
  threads: [],
  activeThread: null,
  activeRunId: null,
  events: [],
  stream: null,
  projection: initialAgentProjection(),
};

const threadList = document.querySelector("#thread-list");
const transcript = document.querySelector("#transcript");
const composer = document.querySelector("#composer");
const messageInput = document.querySelector("#message");
const fileInput = document.querySelector("#file-input");
const runStatus = document.querySelector("#run-status");
const evidenceList = document.querySelector("#evidence-list");
const fileList = document.querySelector("#file-list");
const guidanceInput = document.querySelector("#guidance-input");
const guidanceButton = document.querySelector("#send-guidance");
const cancelButton = document.querySelector("#cancel-run");

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed (${response.status})`);
  }
  if (response.status === 204) return null;
  return response.json();
}

function renderThreads() {
  threadList.replaceChildren();
  for (const thread of state.threads) {
    const button = document.createElement("button");
    button.className = `thread-row${thread.id === state.activeThread?.id ? " active" : ""}`;
    button.textContent = thread.title;
    button.type = "button";
    button.addEventListener("click", () => selectThread(thread).catch(showError));
    threadList.append(button);
  }
}

function contentText(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === "string") return item;
        if (item?.type === "text") return item.text || "";
        return contentText(item?.content ?? item?.text ?? "");
      })
      .join("");
  }
  if (!value || typeof value !== "object") return "";
  return contentText(value.content ?? value.value ?? value.text ?? "");
}

function eventText(event) {
  const payload = event.payload || {};
  if (["message.delta", "message.completed"].includes(event.type)) {
    return contentText(payload);
  }
  if (event.type === "run.error") return payload.message || payload.error || "Run failed";
  if (event.type === "todo.updated") return "Research plan updated";
  if (event.type === "sandbox.bound") return "Daytona workspace ready";
  if (event.type === "artifact.updated") return `Artifact ready: ${payload.name || "file"}`;
  if (event.type === "widget.ready") {
    return `Widget ready: ${payload.widget?.title || payload.title || "result"}`;
  }
  if (event.type === "usage.updated") {
    const tokens = payload.total_tokens ?? "unknown";
    const cost = payload.estimated_cost_usd;
    return cost == null
      ? `Usage: ${tokens} tokens`
      : `Usage: ${tokens} tokens · $${Number(cost).toFixed(4)}`;
  }
  return event.type.replaceAll(".", " ");
}

function messageProjection() {
  const projected = [];
  const completedRuns = new Set(
    state.events
      .filter((event) => event.type === "message.completed")
      .map((event) => event.run_id),
  );
  const deltas = new Map();

  for (const event of state.events) {
    if (event.type === "user.message") {
      projected.push({ ...event, author: "You", text: event.payload.content });
    } else if (event.type === "message.completed") {
      projected.push({ ...event, author: "LangAlpha", text: eventText(event) });
    } else if (event.type === "message.delta" && !completedRuns.has(event.run_id)) {
      const current = deltas.get(event.run_id) || { ...event, author: "LangAlpha", text: "" };
      current.text += eventText(event);
      deltas.set(event.run_id, current);
    } else if (event.type === "run.error") {
      projected.push({ ...event, author: "LangAlpha", text: eventText(event) });
    }
  }
  projected.push(...deltas.values());
  return projected;
}

function interruptValue(event) {
  const payload = event.payload || {};
  const values = payload.__interrupt__ || payload.interrupts || [];
  const first = Array.isArray(values) ? values[0] : values;
  return first?.value || first || payload;
}

async function resumeInterrupt(event, value) {
  setStatus("Resuming analysis");
  const threadId = state.activeThread.id;
  const run = await api(`/api/threads/${threadId}/runs/${event.run_id}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  state.activeRunId = run.id;
  connectRun(threadId, run.id);
}

function renderInterrupt(event) {
  const value = interruptValue(event);
  const card = document.createElement("section");
  card.className = "interrupt-card";
  const title = document.createElement("h3");
  title.textContent = value.kind === "plan" ? "Plan approval" : "LangAlpha needs input";
  card.append(title);

  const detail = document.createElement("p");
  detail.textContent = value.question || value.goal || "Review the request before continuing.";
  card.append(detail);

  if (value.kind === "plan" && Array.isArray(value.steps)) {
    const list = document.createElement("ol");
    for (const step of value.steps) {
      const item = document.createElement("li");
      item.textContent = `${step.title}: ${step.description}`;
      list.append(item);
    }
    card.append(list);
    const actions = document.createElement("div");
    actions.className = "interrupt-actions";
    const approve = document.createElement("button");
    approve.className = "primary";
    approve.type = "button";
    approve.textContent = "Approve";
    approve.addEventListener("click", () =>
      resumeInterrupt(event, { decision: "approve" }).catch(showError),
    );
    const reject = document.createElement("button");
    reject.className = "secondary";
    reject.type = "button";
    reject.textContent = "Reject";
    reject.addEventListener("click", () =>
      resumeInterrupt(event, { decision: "reject" }).catch(showError),
    );
    actions.append(approve, reject);
    card.append(actions);
  } else {
    const form = document.createElement("form");
    form.className = "interrupt-answer";
    const input = document.createElement("textarea");
    input.required = true;
    input.rows = 3;
    input.placeholder = "Your answer…";
    const button = document.createElement("button");
    button.className = "primary";
    button.type = "submit";
    button.textContent = "Continue";
    form.append(input, button);
    form.addEventListener("submit", (submitEvent) => {
      submitEvent.preventDefault();
      resumeInterrupt(event, input.value.trim()).catch(showError);
    });
    card.append(form);
  }
  transcript.append(card);
}

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const CHART_COLORS = ["#14756f", "#c37b2a", "#6b5ca5", "#b54b62", "#4878a8", "#748238"];

function svgElement(name, attributes = {}) {
  const element = document.createElementNS(SVG_NAMESPACE, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function renderWidgetTable(rows) {
  const safeRows = rows.filter(
    (row) => row && typeof row === "object" && !Array.isArray(row),
  );
  const wrapper = document.createElement("div");
  wrapper.className = "widget-table-wrap";
  if (!safeRows.length) return wrapper;

  const columns = [...new Set(safeRows.flatMap((row) => Object.keys(row)))].slice(0, 8);
  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headerRow = document.createElement("tr");
  for (const column of columns) {
    const cell = document.createElement("th");
    cell.textContent = column;
    headerRow.append(cell);
  }
  head.append(headerRow);
  const body = document.createElement("tbody");
  for (const row of safeRows) {
    const tableRow = document.createElement("tr");
    for (const column of columns) {
      const cell = document.createElement("td");
      cell.textContent = String(row[column] ?? "");
      tableRow.append(cell);
    }
    body.append(tableRow);
  }
  table.append(head, body);
  wrapper.append(table);
  return wrapper;
}

function renderChart(widget) {
  const model = buildChartModel(widget);
  const width = 680;
  const height = 300;
  const padding = { top: 20, right: 20, bottom: 58, left: 58 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const yRange = model.maximum - model.minimum;
  const xCenter = (index) =>
    padding.left + ((index + 0.5) * plotWidth) / model.labels.length;
  const yPosition = (value) =>
    padding.top + ((model.maximum - value) / yRange) * plotHeight;
  const numberFormat = new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  });

  const wrapper = document.createElement("div");
  wrapper.className = "widget-chart-wrap";
  const svg = svgElement("svg", {
    class: "widget-chart",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `${widget.title || "Data"} ${model.kind} chart`,
  });
  const title = svgElement("title");
  title.textContent = widget.title || "Data chart";
  svg.append(title);

  for (let index = 0; index <= 4; index += 1) {
    const value = model.maximum - (index * yRange) / 4;
    const y = yPosition(value);
    svg.append(
      svgElement("line", {
        class: "chart-grid",
        x1: padding.left,
        x2: width - padding.right,
        y1: y,
        y2: y,
      }),
    );
    const label = svgElement("text", {
      class: "chart-label chart-y-label",
      x: padding.left - 9,
      y: y + 4,
      "text-anchor": "end",
    });
    label.textContent = numberFormat.format(value);
    svg.append(label);
  }

  const baseline = yPosition(0);
  svg.append(
    svgElement("line", {
      class: "chart-axis",
      x1: padding.left,
      x2: width - padding.right,
      y1: baseline,
      y2: baseline,
    }),
  );

  const xLabelStep = Math.max(1, Math.ceil(model.labels.length / 8));
  model.labels.forEach((value, index) => {
    if (index % xLabelStep !== 0 && index !== model.labels.length - 1) return;
    const label = svgElement("text", {
      class: "chart-label chart-x-label",
      x: xCenter(index),
      y: height - padding.bottom + 23,
      "text-anchor": "middle",
    });
    label.textContent = value.length > 16 ? `${value.slice(0, 15)}…` : value;
    svg.append(label);
  });

  if (model.kind === "bar") {
    const groupWidth = plotWidth / model.labels.length;
    const barWidth = Math.max(1, (groupWidth * 0.72) / model.series.length);
    model.series.forEach((series, seriesIndex) => {
      series.values.forEach((value, rowIndex) => {
        if (value === null) return;
        const valueY = yPosition(value);
        svg.append(
          svgElement("rect", {
            class: "chart-bar",
            x:
              padding.left +
              rowIndex * groupWidth +
              groupWidth * 0.14 +
              seriesIndex * barWidth,
            y: Math.min(valueY, baseline),
            width: Math.max(1, barWidth - 1),
            height: Math.max(1, Math.abs(baseline - valueY)),
            fill: CHART_COLORS[seriesIndex % CHART_COLORS.length],
          }),
        );
      });
    });
  } else {
    model.series.forEach((series, seriesIndex) => {
      let drawing = false;
      let pathData = "";
      series.values.forEach((value, rowIndex) => {
        if (value === null) {
          drawing = false;
          return;
        }
        pathData += `${drawing ? " L" : " M"} ${xCenter(rowIndex)} ${yPosition(value)}`;
        drawing = true;
      });
      if (pathData) {
        svg.append(
          svgElement("path", {
            class: "chart-line",
            d: pathData,
            stroke: CHART_COLORS[seriesIndex % CHART_COLORS.length],
          }),
        );
      }
      if (model.labels.length <= 20) {
        series.values.forEach((value, rowIndex) => {
          if (value === null) return;
          svg.append(
            svgElement("circle", {
              class: "chart-point",
              cx: xCenter(rowIndex),
              cy: yPosition(value),
              r: 3,
              fill: CHART_COLORS[seriesIndex % CHART_COLORS.length],
            }),
          );
        });
      }
    });
  }

  wrapper.append(svg);
  const legend = document.createElement("div");
  legend.className = "widget-legend";
  model.series.forEach((series, index) => {
    const item = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.style.backgroundColor = CHART_COLORS[index % CHART_COLORS.length];
    item.append(swatch, document.createTextNode(series.field));
    legend.append(item);
  });
  wrapper.append(legend);
  return wrapper;
}

function renderWidget(event) {
  const widget = event.payload?.widget || event.payload || {};
  const card = document.createElement("section");
  card.className = "widget-card";

  const heading = document.createElement("div");
  heading.className = "widget-heading";
  const title = document.createElement("h3");
  title.textContent = widget.title || "Result";
  const kind = document.createElement("span");
  kind.textContent = widget.kind || "data";
  heading.append(title, kind);
  card.append(heading);

  if (widget.description) {
    const description = document.createElement("p");
    description.textContent = widget.description;
    card.append(description);
  }

  const rows = Array.isArray(widget.data) ? widget.data.slice(0, 50) : [];
  if (widget.kind === "metric" && rows.length) {
    const metrics = document.createElement("dl");
    metrics.className = "widget-metrics";
    for (const [label, value] of Object.entries(rows[0]).slice(0, 8)) {
      const item = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      detail.textContent = String(value ?? "—");
      item.append(term, detail);
      metrics.append(item);
    }
    card.append(metrics);
  } else if (["bar", "line"].includes(widget.kind) && rows.length) {
    try {
      card.append(renderChart({ ...widget, data: rows }));
    } catch {
      card.append(renderWidgetTable(rows));
    }
  } else if (rows.length) {
    card.append(renderWidgetTable(rows));
  }
  transcript.append(card);
}

function renderEvents() {
  if (!state.events.length) {
    transcript.innerHTML = `
      <div class="empty-state">
        <h2>${state.activeThread ? state.activeThread.title : "Start a research thread"}</h2>
        <p>Ask a question or attach a file. The workspace remains empty until you do.</p>
      </div>`;
    return;
  }

  const progressEvents = state.events.filter(
    (event) =>
      !["user.message", "message.delta", "message.completed"].includes(event.type),
  );

  transcript.replaceChildren();
  if (progressEvents.length) {
    const progress = document.createElement("section");
    progress.className = "progress";
    const latest = progressEvents.at(-1);
    progress.innerHTML = `
      <h3>Tool / data progress</h3>
      <div class="progress-row">
        <span class="progress-mark active"></span>
        <span></span>
      </div>`;
    progress.querySelector(".progress-row span:last-child").textContent = eventText(latest);
    transcript.append(progress);
  }

  for (const message of messageProjection()) {
    const article = document.createElement("article");
    article.className = `message ${message.author === "LangAlpha" ? "assistant" : ""}`;
    const created = new Date(message.created_at).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
    article.innerHTML = `
      <div class="message-head"><strong></strong><time>${created}</time></div>
      <div class="message-body"></div>`;
    article.querySelector("strong").textContent = message.author;
    article.querySelector(".message-body").textContent = message.text;
    transcript.append(article);
  }

  for (const widget of state.events.filter((event) => event.type === "widget.ready")) {
    renderWidget(widget);
  }

  const unresolved = state.events.filter(
    (event) =>
      event.type === "interrupt.requested" &&
      !state.events.some(
        (candidate) =>
          candidate.type === "interrupt.resumed" &&
          candidate.payload?.parent_run_id === event.run_id,
      ),
  );
  if (unresolved.length) renderInterrupt(unresolved.at(-1));
  transcript.scrollTop = transcript.scrollHeight;

  evidenceList.replaceChildren();
  for (const event of progressEvents.slice(-8).reverse()) {
    const row = document.createElement("div");
    row.className = "rail-item";
    row.innerHTML = `
      <span class="rail-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M12 3v7m0 4v7M3 12h7m4 0h7"/><circle cx="12" cy="12" r="2.5"/></svg>
      </span><span></span>`;
    row.lastElementChild.textContent = eventText(event);
    evidenceList.append(row);
  }
}

function setStatus(label, mode = "active") {
  runStatus.className = `status-row${mode === "error" ? " error" : ""}`;
  runStatus.innerHTML = `<span class="status-mark ${mode === "active" ? "active" : ""}"></span><span></span>`;
  runStatus.lastElementChild.textContent = label;
  guidanceButton.disabled = !state.activeRunId;
  cancelButton.disabled = !state.activeRunId;
}

function recordEvent(event) {
  state.projection = reduceAgentEvent(state.projection, event);
  state.events = state.projection.events;
  state.activeRunId = state.projection.activeRunId;
  setStatus(state.projection.status.label, state.projection.status.mode);
}

function connectRun(threadId, runId) {
  state.stream?.close();
  state.stream = new EventSource(`/api/threads/${threadId}/runs/${runId}/stream`);
  const types = [
    "run.success",
    "run.error",
    "run.interrupted",
    "run.cancelled",
    "message.delta",
    "message.completed",
    "state.updated",
    "agent.custom",
    "agent.metadata",
    "sandbox.bound",
    "artifact.updated",
    "widget.ready",
    "interrupt.requested",
    "steering.delivered",
  ];
  for (const type of types) {
    state.stream.addEventListener(type, (incoming) => {
      const event = JSON.parse(incoming.data);
      recordEvent(event);
      if (type === "artifact.updated") addFile(event.payload);
      if (type.startsWith("run.")) state.stream?.close();
      renderEvents();
    });
  }
}

function snapshotEvent(threadId, runId, type, payload, id) {
  return {
    id,
    thread_id: threadId,
    run_id: runId,
    type,
    payload,
    created_at: new Date().toISOString(),
  };
}

async function loadSnapshot(threadId) {
  const snapshot = await api(`/api/threads/${threadId}/snapshot`);
  state.projection = initialAgentProjection();
  state.events = [];
  const fallbackRunId = snapshot.runs[0]?.id || `snapshot:${threadId}`;
  for (const message of snapshot.messages) {
    if (message.role === "user") {
      recordEvent(
        snapshotEvent(
          threadId,
          fallbackRunId,
          "user.message",
          { content: message.content },
          `snapshot:message:${message.id}`,
        ),
      );
    } else if (message.role === "assistant") {
      recordEvent(
        snapshotEvent(
          threadId,
          fallbackRunId,
          "message.completed",
          message,
          `snapshot:message:${message.id}`,
        ),
      );
    }
  }
  for (const widget of snapshot.widgets) {
    recordEvent(
      snapshotEvent(
        threadId,
        fallbackRunId,
        "widget.ready",
        { widget },
        `snapshot:widget:${widget.id}`,
      ),
    );
  }
  if (snapshot.todos.length) {
    recordEvent(
      snapshotEvent(
        threadId,
        fallbackRunId,
        "todo.updated",
        { todos: snapshot.todos },
        `snapshot:todos:${threadId}`,
      ),
    );
  }
  if (snapshot.usage.total_tokens) {
    recordEvent(
      snapshotEvent(
        threadId,
        fallbackRunId,
        "usage.updated",
        snapshot.usage,
        `snapshot:usage:${threadId}`,
      ),
    );
  }
  const interrupted = snapshot.runs.find((run) => run.status === "interrupted");
  if (snapshot.interrupts.length && interrupted) {
    recordEvent(
      snapshotEvent(
        threadId,
        interrupted.id,
        "interrupt.requested",
        { interrupts: snapshot.interrupts },
        `snapshot:interrupt:${interrupted.id}`,
      ),
    );
  }
  fileList.replaceChildren();
  if (!snapshot.artifacts.length) {
    fileList.innerHTML = '<p class="muted">No files yet.</p>';
  } else {
    for (const artifact of [...snapshot.artifacts].reverse()) addFile(artifact);
  }
  const active = snapshot.runs.find((run) =>
    ["pending", "running"].includes(run.status),
  );
  if (active) {
    recordEvent(
      snapshotEvent(
        threadId,
        active.id,
        "run.started",
        active,
        `snapshot:run:${active.id}`,
      ),
    );
    connectRun(threadId, active.id);
  } else if (!interrupted && snapshot.runs.length) {
    const latest = snapshot.runs[0];
    recordEvent(
      snapshotEvent(
        threadId,
        latest.id,
        `run.${latest.status}`,
        latest,
        `snapshot:terminal:${latest.id}:${latest.status}`,
      ),
    );
  }
  renderEvents();
}

async function selectThread(thread) {
  state.activeThread = thread;
  state.activeRunId = null;
  state.stream?.close();
  evidenceList.innerHTML = '<p class="muted">Tool and data events will appear here.</p>';
  fileList.innerHTML = '<p class="muted">No files yet.</p>';
  setStatus("Ready", "idle");
  renderThreads();
  await loadSnapshot(thread.id);
}

async function createThread() {
  const thread = await api("/api/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: "New research" }),
  });
  state.threads.unshift(thread);
  await selectThread(thread);
}

async function ensureThread() {
  if (!state.activeThread) await createThread();
  return state.activeThread;
}

async function submitMessage(event) {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) return;
  const thread = await ensureThread();
  setStatus("Submitting");
  const run = await api(`/api/threads/${thread.id}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  state.activeRunId = run.id;
  recordEvent(
    snapshotEvent(
      thread.id,
      run.id,
      "user.message",
      { content: message },
      `local:user:${run.id}`,
    ),
  );
  recordEvent(
    snapshotEvent(thread.id, run.id, "run.started", run, `local:run:${run.id}`),
  );
  connectRun(thread.id, run.id);
  renderEvents();
  messageInput.value = "";
  setStatus("Analysis running");
}

function addFile(artifact) {
  if (!artifact?.id || fileList.querySelector(`[data-artifact-id="${artifact.id}"]`)) return;
  if (fileList.querySelector(".muted")) fileList.replaceChildren();
  const link = document.createElement("a");
  link.className = "rail-item";
  link.dataset.artifactId = artifact.id;
  link.href = `/api/artifacts/${artifact.id}`;
  link.innerHTML = `
    <span class="rail-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="M6 2.5h8l4 4V21.5H6z"/><path d="M14 2.5v4h4M9 12h6M9 16h6"/></svg>
    </span><span></span>`;
  link.lastElementChild.textContent = artifact.name;
  fileList.prepend(link);
}

async function uploadFile() {
  const file = fileInput.files?.[0];
  if (!file) return;
  const thread = await ensureThread();
  setStatus("Uploading file");
  const body = new FormData();
  body.append("file", file);
  const artifact = await api(`/api/threads/${thread.id}/files`, {
    method: "POST",
    body,
  });
  addFile(artifact);
  fileInput.value = "";
  setStatus("Ready", "idle");
}

async function sendGuidance() {
  const message = guidanceInput.value.trim();
  if (!message || !state.activeRunId) return;
  const threadId = state.activeThread.id;
  await api(`/api/threads/${threadId}/runs/${state.activeRunId}/guidance`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  guidanceInput.value = "";
  setStatus("Guidance queued");
}

async function cancelRun() {
  if (!state.activeRunId) return;
  const threadId = state.activeThread.id;
  await api(`/api/threads/${threadId}/runs/${state.activeRunId}/cancel`, {
    method: "POST",
  });
  state.activeRunId = null;
  setStatus("Run cancelled", "idle");
}

function showError(error) {
  setStatus(error.message || String(error), "error");
}

async function initialize() {
  state.threads = await api("/api/threads");
  renderThreads();
  setStatus("Ready", "idle");
  if (state.threads.length) await selectThread(state.threads[0]);
}

document.querySelector("#new-thread").addEventListener("click", createThread);
composer.addEventListener("submit", submitMessage);
fileInput.addEventListener("change", uploadFile);
guidanceButton.addEventListener("click", () => sendGuidance().catch(showError));
cancelButton.addEventListener("click", () => cancelRun().catch(showError));
initialize().catch(showError);
