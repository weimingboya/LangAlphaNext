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
  UsageSummary,
  Widget,
} from "./types";

const TERMINAL_RUN_EVENTS = new Set(["run.success", "run.error", "run.cancelled"]);
const TOOL_LABELS: Record<string, string> = {
  ask_user: "Request clarification",
  check_async_task: "Check research task",
  fred_get_observations: "Fetch macroeconomic observations",
  fred_search_series: "Search FRED series",
  inspect_asset: "Inspect workspace file",
  market_get_bars: "Fetch market price history",
  market_get_corporate_actions: "Fetch corporate actions",
  market_get_snapshots: "Fetch market snapshots",
  market_resolve_instrument: "Resolve market instrument",
  materialize_dataset: "Prepare research dataset",
  sec_get_company_facts: "Fetch SEC company facts",
  sec_get_filing: "Read SEC filing",
  sec_list_filings: "List SEC filings",
  sec_resolve_company: "Resolve SEC company",
  show_widget: "Build result widget",
  start_async_task: "Start research task",
  submit_plan: "Submit research plan",
  web_search: "Search the web",
  web_search_call: "Search the web",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function toolLabel(name: string): string {
  if (TOOL_LABELS[name]) return TOOL_LABELS[name];
  const words = name
    .replace(/^mcp__/, "")
    .replaceAll("__", " · ")
    .replaceAll("_", " ")
    .trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "Use research tool";
}

function parseToolArguments(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}

function compactToolDetail(value: unknown): string | undefined {
  const parsed = parseToolArguments(value);
  if (!isRecord(parsed)) {
    if (typeof parsed !== "string") return undefined;
    return parsed.length > 140 ? `${parsed.slice(0, 137)}…` : parsed;
  }
  const preferredKeys = [
    "query",
    "symbol",
    "symbols",
    "cik",
    "forms",
    "series_id",
    "series_ids",
    "start_date",
    "end_date",
    "agent_name",
    "description",
    "task_id",
    "path",
    "filename",
  ];
  const values = preferredKeys.flatMap((key) => {
    const current = parsed[key];
    if (current === undefined || current === null || current === "") return [];
    const rendered = Array.isArray(current)
      ? current.slice(0, 4).map(String).join(", ")
      : String(current);
    return rendered ? [rendered] : [];
  });
  if (!values.length) return undefined;
  const detail = values.join(" · ");
  return detail.length > 140 ? `${detail.slice(0, 137)}…` : detail;
}

interface ToolActivityCandidate extends ActivityItem {
  callId: string;
  toolName?: string;
}

function toolCallCandidate(
  value: unknown,
  event: AgentEvent,
  index: number,
): ToolActivityCandidate | null {
  if (!isRecord(value)) return null;
  const nestedFunction = isRecord(value.function) ? value.function : null;
  const name = String(
    value.name ||
      value.tool_name ||
      nestedFunction?.name ||
      (String(value.type || "").toLowerCase() === "web_search_call"
        ? "web_search"
        : ""),
  );
  if (!name) return null;
  const callId = String(value.call_id || value.id || `${event.id}:${index}:${name}`);
  const rawStatus = String(value.status || "").toLowerCase();
  const complete = ["completed", "complete", "success", "succeeded"].includes(rawStatus);
  const action = isRecord(value.action) ? value.action : null;
  const detail = compactToolDetail(
    value.args ??
      value.arguments ??
      value.input ??
      nestedFunction?.arguments ??
      action ??
      null,
  );
  return {
    id: `tool:${event.run_id}:${callId}`,
    callId,
    toolName: name,
    title: toolLabel(name),
    ...(detail ? { detail } : {}),
    status: complete ? "complete" : "running",
    created_at: event.created_at,
  };
}

function toolResultCandidate(
  value: Record<string, unknown>,
  event: AgentEvent,
  index: number,
): ToolActivityCandidate {
  const callId = String(
    value.tool_call_id || value.call_id || value.id || `${event.id}:${index}:result`,
  );
  const name = typeof value.name === "string" ? value.name : "";
  const rawStatus = String(value.status || "success").toLowerCase();
  return {
    id: `tool:${event.run_id}:${callId}`,
    callId,
    ...(name ? { toolName: name } : {}),
    title: name ? toolLabel(name) : "Research tool",
    detail: rawStatus === "error" ? "Tool returned an error" : "Completed",
    status: rawStatus === "error" ? "error" : "complete",
    created_at: event.created_at,
  };
}

function messageToolCandidates(event: AgentEvent): ToolActivityCandidate[] {
  const candidates: ToolActivityCandidate[] = [];
  let candidateIndex = 0;

  function visit(value: unknown, depth = 0): void {
    if (depth > 7) return;
    if (Array.isArray(value)) {
      for (const item of value) visit(item, depth + 1);
      return;
    }
    if (!isRecord(value)) return;

    const kind = messageKind(value);
    if (kind === "tool") {
      candidates.push(toolResultCandidate(value, event, candidateIndex++));
      return;
    }
    if (kind === "assistant") {
      const calls = Array.isArray(value.tool_calls)
        ? value.tool_calls
        : Array.isArray(value.tool_call_chunks)
          ? value.tool_call_chunks
          : [];
      for (const call of calls) {
        const candidate = toolCallCandidate(call, event, candidateIndex++);
        if (candidate) candidates.push(candidate);
      }
      if (Array.isArray(value.content)) {
        for (const block of value.content) {
          if (!isRecord(block)) continue;
          const blockType = String(block.type || "").toLowerCase();
          if (
            blockType === "web_search_call" ||
            blockType === "mcp_call" ||
            blockType.includes("tool_use") ||
            blockType.includes("function_call")
          ) {
            const candidate = toolCallCandidate(block, event, candidateIndex++);
            if (candidate) candidates.push(candidate);
          }
        }
      }
      return;
    }

    if ("messages" in value) visit(value.messages, depth + 1);
    if ("message" in value) visit(value.message, depth + 1);
    if ("value" in value) visit(value.value, depth + 1);
    if (event.type === "state.updated") {
      for (const nested of Object.values(value)) visit(nested, depth + 1);
    }
  }

  visit(event.payload);
  return candidates;
}

function standaloneActivity(event: AgentEvent): ActivityItem | null {
  if (event.type === "todo.updated") {
    return {
      id: event.id,
      title: "Update research plan",
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
  const items = new Map<string, ToolActivityCandidate | ActivityItem>();
  for (const event of events) {
    for (const candidate of messageToolCandidates(event)) {
      const current = items.get(candidate.id);
      items.set(candidate.id, {
        ...current,
        ...candidate,
        title:
          candidate.toolName || !current
            ? candidate.title
            : current.title,
        detail:
          candidate.status === "complete" && current?.detail
            ? current.detail
            : candidate.detail || current?.detail,
        created_at: current?.created_at || candidate.created_at,
      });
    }
    const standalone = standaloneActivity(event);
    if (standalone) items.set(standalone.id, standalone);
  }
  return [...items.values()];
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
