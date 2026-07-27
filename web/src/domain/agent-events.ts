import type {
  ActivityItem,
  AgentEvent,
  AgentProjection,
  AgentStatus,
  ChartModel,
  Citation,
  JsonObject,
  MessageContent,
  ProjectedMessage,
  TodoItem,
  UsageSummary,
  Widget,
} from "./types";

const TERMINAL_RUN_EVENTS = new Set(["run.success", "run.error", "run.cancelled"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function standaloneActivity(event: AgentEvent): ActivityItem | null {
  if (event.type === "activity.updated") {
    const id = typeof event.payload.id === "string" ? event.payload.id : "";
    const title = typeof event.payload.title === "string" ? event.payload.title : "";
    const status = event.payload.status;
    if (
      !id ||
      !title ||
      !["running", "complete", "error", "info"].includes(String(status))
    ) {
      return null;
    }
    return {
      id,
      title,
      ...(typeof event.payload.replaces_id === "string"
        ? { replaces_id: event.payload.replaces_id }
        : {}),
      ...(event.payload.kind === "reasoning" ||
      event.payload.kind === "tool" ||
      event.payload.kind === "subagent"
        ? { kind: event.payload.kind }
        : {}),
      ...(typeof event.payload.detail === "string"
        ? { detail: event.payload.detail }
        : {}),
      status: status as ActivityItem["status"],
      created_at: event.created_at,
    };
  }
  if (event.type === "todo.updated") {
    const todos = Array.isArray(event.payload.todos) ? event.payload.todos : [];
    const completed = todos.filter(
      (todo) => isRecord(todo) && todo.status === "completed",
    ).length;
    return {
      id: `todo:${event.run_id}`,
      title: "Update research plan",
      ...(todos.length
        ? { detail: `${completed}/${todos.length} complete` }
        : {}),
      status: "complete",
      created_at: event.created_at,
    };
  }
  if (event.type === "sandbox.bound") {
    return {
      id: event.id,
      title: "Prepare secure workspace",
      status: "complete",
      created_at: event.created_at,
    };
  }
  if (event.type === "asset.ready") {
    return {
      id: event.id,
      title: "Create research file",
      detail: String(event.payload.filename || "Artifact ready"),
      status: "complete",
      created_at: event.created_at,
    };
  }
  if (event.type === "asset.failed") {
    return {
      id: event.id,
      title: "Create research file",
      detail: "Artifact generation failed",
      status: "error",
      created_at: event.created_at,
    };
  }
  if (event.type === "widget.ready") {
    const widget = isRecord(event.payload.widget) ? event.payload.widget : event.payload;
    return {
      id: event.id,
      title: "Build result widget",
      detail: String(widget.title || "Widget ready"),
      status: "complete",
      created_at: event.created_at,
    };
  }
  if (event.type === "run.error") {
    return {
      id: event.id,
      title: "Research run failed",
      detail: String(event.payload.message || event.payload.error || "Unknown error"),
      status: "error",
      created_at: event.created_at,
    };
  }
  return null;
}

export function projectActivity(events: AgentEvent[]): ActivityItem[] {
  const items = new Map<string, ActivityItem>();

  function merge(candidate: ActivityItem): void {
    const replaced = candidate.replaces_id
      ? items.get(candidate.replaces_id)
      : undefined;
    if (candidate.replaces_id) items.delete(candidate.replaces_id);
    const current = items.get(candidate.id) || replaced;
    const meaningfulResult =
      candidate.status === "complete" &&
      candidate.detail &&
      candidate.detail !== "Completed";
    const detail =
      candidate.kind === "reasoning"
        ? candidate.detail || current?.detail
        : meaningfulResult && current?.detail && current.detail !== candidate.detail
        ? `${current.detail} · ${candidate.detail}`
        : meaningfulResult
          ? candidate.detail
          : candidate.status === "running"
            ? candidate.detail || current?.detail
            : current?.detail || candidate.detail;
    const publicCandidate = { ...candidate };
    delete publicCandidate.replaces_id;
    items.set(candidate.id, {
      ...current,
      ...publicCandidate,
      title: candidate.title || current?.title || "Research activity",
      ...(detail ? { detail } : {}),
      created_at: current?.created_at || candidate.created_at,
    });
  }

  for (const event of events) {
    const standalone = standaloneActivity(event);
    if (standalone) merge(standalone);
  }
  return [...items.values()];
}

export function projectTodos(events: AgentEvent[]): TodoItem[] {
  let todos: TodoItem[] = [];
  for (const event of events) {
    if (event.type !== "todo.updated" || !Array.isArray(event.payload.todos)) {
      continue;
    }
    todos = event.payload.todos.flatMap((value) => {
      if (!isRecord(value) || typeof value.content !== "string") return [];
      if (
        value.status !== "pending" &&
        value.status !== "in_progress" &&
        value.status !== "completed"
      ) {
        return [];
      }
      return [{ content: value.content, status: value.status }];
    });
  }
  return todos;
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`AgentEvent.${field} must be a non-empty string`);
  }
  return value;
}

function finiteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function messageKind(value: unknown): "" | "assistant" | "user" | "tool" {
  if (!isRecord(value)) return "";
  const raw = String(value.role || value.type || "").toLowerCase();
  if (raw === "assistant" || raw === "ai" || raw.includes("aimessage")) {
    return "assistant";
  }
  if (raw === "user" || raw === "human" || raw.includes("humanmessage")) {
    return "user";
  }
  if (raw === "tool" || raw.includes("toolmessage")) return "tool";
  return "";
}

function visibleText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(visibleText).join("");
  if (!isRecord(value)) return "";
  const kind = String(value.type || "").toLowerCase();
  if (["text", "output_text"].includes(kind)) {
    return visibleText(value.text ?? value.content ?? "");
  }
  if (kind.includes("tool") || kind.includes("reasoning") || kind.includes("function")) {
    return "";
  }
  return visibleText(value.content ?? value.text ?? "");
}

function urlCitation(annotation: unknown, offset = 0): Citation | null {
  if (!isRecord(annotation)) return null;
  const nested = isRecord(annotation.url_citation) ? annotation.url_citation : annotation;
  if (String(annotation.type || nested.type || "").toLowerCase() !== "url_citation") {
    return null;
  }
  if (typeof nested.url !== "string" || !nested.url) return null;
  const start = finiteNumber(nested.start_index);
  const end = finiteNumber(nested.end_index);
  return {
    url: nested.url,
    title: typeof nested.title === "string" ? nested.title : nested.url,
    start_index: start === null ? null : offset + start,
    end_index: end === null ? null : offset + end,
  };
}

function visibleContent(value: unknown): MessageContent {
  if (typeof value === "string") return { text: value, citations: [] };
  if (Array.isArray(value)) {
    let text = "";
    const citations: Citation[] = [];
    for (const item of value) {
      const current = visibleContent(item);
      const offset = text.length;
      text += current.text;
      citations.push(
        ...current.citations.map((citation) => ({
          ...citation,
          start_index:
            citation.start_index === null ? null : citation.start_index + offset,
          end_index: citation.end_index === null ? null : citation.end_index + offset,
        })),
      );
    }
    return { text, citations };
  }
  if (!isRecord(value)) return { text: "", citations: [] };
  const kind = String(value.type || "").toLowerCase();
  if (kind.includes("tool") || kind.includes("reasoning") || kind.includes("function")) {
    return { text: "", citations: [] };
  }
  if (["text", "output_text"].includes(kind)) {
    const text = visibleText(value.text ?? value.content ?? "");
    const citations = Array.isArray(value.annotations)
      ? value.annotations
          .map((annotation) => urlCitation(annotation))
          .filter((citation): citation is Citation => citation !== null)
      : [];
    return { text, citations };
  }
  return visibleContent(value.content ?? value.text ?? "");
}

export function assistantMessageContent(value: unknown): MessageContent {
  if (Array.isArray(value)) {
    const messages = value.filter((item) => messageKind(item));
    if (messages.length) {
      const latestAssistant = messages.findLast(
        (item) => messageKind(item) === "assistant" && visibleText(item).trim(),
      );
      return latestAssistant
        ? assistantMessageContent(latestAssistant)
        : { text: "", citations: [] };
    }
    return visibleContent(value);
  }
  if (!isRecord(value)) return { text: "", citations: [] };

  const kind = messageKind(value);
  if (kind === "tool" || kind === "user") return { text: "", citations: [] };
  if (kind === "assistant") return visibleContent(value.content ?? "");

  if ("value" in value) return assistantMessageContent(value.value);
  if ("message" in value) return assistantMessageContent(value.message);
  if ("messages" in value) return assistantMessageContent(value.messages);
  return visibleContent(value);
}

export function assistantMessageText(value: unknown): string {
  return assistantMessageContent(value).text;
}

function compactNumber(value: number): string {
  return new Intl.NumberFormat("en", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

export function formatUsageSummary(
  payload: Partial<UsageSummary> | Record<string, unknown>,
): string {
  const input = finiteNumber(payload.input_tokens);
  const output = finiteNumber(payload.output_tokens);
  const cached = finiteNumber(payload.cached_input_tokens);
  const total = finiteNumber(payload.total_tokens);
  const cost = finiteNumber(payload.estimated_cost_usd);
  const webSearches = finiteNumber(payload.web_search_calls);
  const parts: string[] = [];

  if (input !== null) {
    const cachedLabel =
      cached !== null && cached > 0 ? ` (${compactNumber(cached)} cached)` : "";
    parts.push(`${compactNumber(input)} input${cachedLabel}`);
  }
  if (output !== null) parts.push(`${compactNumber(output)} output`);
  if (!parts.length && total !== null) parts.push(`${compactNumber(total)} total`);
  if (webSearches !== null && webSearches > 0) {
    parts.push(`${compactNumber(webSearches)} web searches`);
  }
  if (cost !== null) parts.push(`$${cost.toFixed(4)}`);
  return parts.length ? `Usage: ${parts.join(" · ")}` : "Usage unavailable";
}

export function buildChartModel(widget: Widget): ChartModel {
  if (!["bar", "line"].includes(widget.kind || "")) {
    throw new TypeError("Chart widget kind must be bar or line");
  }
  if (typeof widget.x_field !== "string" || widget.x_field.length === 0) {
    throw new TypeError("Chart widget requires x_field");
  }
  if (!Array.isArray(widget.y_fields) || widget.y_fields.length === 0) {
    throw new TypeError("Chart widget requires at least one y_field");
  }
  const rows = Array.isArray(widget.data) ? widget.data.slice(0, 50) : [];
  if (!rows.length) throw new TypeError("Chart widget requires data rows");
  const xField = widget.x_field;
  const labels = rows.map((row, index) =>
    row[xField] == null ? String(index + 1) : String(row[xField]),
  );
  const series = widget.y_fields.slice(0, 6).map((field) => ({
    field,
    values: rows.map((row) => finiteNumber(row[field])),
  }));
  const values = series.flatMap((entry) =>
    entry.values.filter((value): value is number => value !== null),
  );
  if (!values.length) throw new TypeError("Chart widget contains no numeric y values");
  const minimum = Math.min(0, ...values);
  let maximum = Math.max(0, ...values);
  if (minimum === maximum) maximum = minimum + 1;
  return {
    kind: widget.kind as "bar" | "line",
    labels,
    series,
    minimum,
    maximum,
  };
}

export function parseAgentEvent(value: unknown): AgentEvent {
  if (!isRecord(value)) throw new TypeError("AgentEvent must be an object");
  if (!isRecord(value.payload)) {
    throw new TypeError("AgentEvent.payload must be an object");
  }
  return {
    id: requireString(value.id, "id"),
    thread_id: requireString(value.thread_id, "thread_id"),
    run_id: requireString(value.run_id, "run_id"),
    type: requireString(value.type, "type"),
    payload: value.payload as JsonObject,
    created_at:
      typeof value.created_at === "string" ? value.created_at : new Date().toISOString(),
  };
}

export function initialAgentProjection(): AgentProjection {
  return {
    events: [],
    activeRunId: null,
    status: { label: "Ready", mode: "idle" },
  };
}

function eventStatus(
  event: AgentEvent,
  current: AgentProjection,
): { activeRunId: string | null; status: AgentStatus } {
  if (event.type === "run.started") {
    return {
      activeRunId: event.run_id,
      status: { label: "Analysis running", mode: "active" },
    };
  }
  if (event.type === "run.interrupted" || event.type === "interrupt.requested") {
    return {
      activeRunId: null,
      status: { label: "Waiting for input", mode: "idle" },
    };
  }
  if (TERMINAL_RUN_EVENTS.has(event.type)) {
    let label = "Run complete";
    if (event.type === "run.success") label = "Analysis complete";
    if (event.type === "run.cancelled") label = "Run cancelled";
    if (event.type === "run.error") {
      label =
        (typeof event.payload.message === "string" && event.payload.message) ||
        (typeof event.payload.error === "string" && event.payload.error) ||
        "Run failed";
    }
    return {
      activeRunId: current.activeRunId === event.run_id ? null : current.activeRunId,
      status: {
        label,
        mode: event.type === "run.error" ? "error" : "idle",
      },
    };
  }
  return { activeRunId: current.activeRunId, status: current.status };
}

export function reduceAgentEvent(
  current: AgentProjection,
  rawEvent: unknown,
): AgentProjection {
  const event = parseAgentEvent(rawEvent);
  if (current.events.some((candidate) => candidate.id === event.id)) return current;
  return {
    events: [...current.events, event],
    ...eventStatus(event, current),
  };
}

export function projectMessages(events: AgentEvent[]): ProjectedMessage[] {
  const projected: ProjectedMessage[] = [];
  const deltas = new Map<string, ProjectedMessage>();

  for (const event of events) {
    if (event.type === "user.message") {
      projected.push({
        ...event,
        author: "You",
        text: typeof event.payload.content === "string" ? event.payload.content : "",
      });
      continue;
    }
    if (event.type === "message.completed") {
      const content = assistantMessageContent(event.payload);
      if (content.text.trim()) {
        deltas.delete(event.run_id);
        projected.push({
          ...event,
          author: "LangAlpha",
          text: content.text,
          citations: content.citations,
        });
      }
      continue;
    }
    if (event.type === "message.delta") {
      const content = assistantMessageContent(event.payload);
      if (!content.text) continue;
      const current = deltas.get(event.run_id) || {
        ...event,
        author: "LangAlpha" as const,
        text: "",
      };
      current.text = content.text;
      current.citations = content.citations;
      deltas.set(event.run_id, current);
      continue;
    }
    if (event.type === "run.error") {
      projected.push({
        ...event,
        author: "LangAlpha",
        text:
          (typeof event.payload.message === "string" && event.payload.message) ||
          (typeof event.payload.error === "string" && event.payload.error) ||
          "Run failed",
      });
    }
  }
  projected.push(...deltas.values());
  return projected;
}
