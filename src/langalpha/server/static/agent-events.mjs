const TERMINAL_RUN_EVENTS = new Set([
  "run.success",
  "run.error",
  "run.cancelled",
]);

function requireString(value, field) {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`AgentEvent.${field} must be a non-empty string`);
  }
  return value;
}

function finiteNumber(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function buildChartModel(widget) {
  if (!widget || typeof widget !== "object" || Array.isArray(widget)) {
    throw new TypeError("Chart widget must be an object");
  }
  if (!["bar", "line"].includes(widget.kind)) {
    throw new TypeError("Chart widget kind must be bar or line");
  }
  if (typeof widget.x_field !== "string" || widget.x_field.length === 0) {
    throw new TypeError("Chart widget requires x_field");
  }
  if (!Array.isArray(widget.y_fields) || widget.y_fields.length === 0) {
    throw new TypeError("Chart widget requires at least one y_field");
  }
  const rows = Array.isArray(widget.data)
    ? widget.data
        .filter((row) => row && typeof row === "object" && !Array.isArray(row))
        .slice(0, 50)
    : [];
  if (!rows.length) throw new TypeError("Chart widget requires data rows");
  const labels = rows.map((row, index) =>
    row[widget.x_field] == null ? String(index + 1) : String(row[widget.x_field]),
  );
  const series = widget.y_fields.slice(0, 6).map((field) => ({
    field: String(field),
    values: rows.map((row) => finiteNumber(row[field])),
  }));
  const values = series.flatMap((entry) =>
    entry.values.filter((value) => value !== null),
  );
  if (!values.length) throw new TypeError("Chart widget contains no numeric y values");
  const minimum = Math.min(0, ...values);
  let maximum = Math.max(0, ...values);
  if (minimum === maximum) maximum = minimum + 1;
  return { kind: widget.kind, labels, series, minimum, maximum };
}

export function parseAgentEvent(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("AgentEvent must be an object");
  }
  if (!value.payload || typeof value.payload !== "object" || Array.isArray(value.payload)) {
    throw new TypeError("AgentEvent.payload must be an object");
  }
  return {
    ...value,
    id: requireString(value.id, "id"),
    thread_id: requireString(value.thread_id, "thread_id"),
    run_id: requireString(value.run_id, "run_id"),
    type: requireString(value.type, "type"),
  };
}

export function initialAgentProjection() {
  return {
    events: [],
    activeRunId: null,
    status: { label: "Ready", mode: "idle" },
  };
}

function eventStatus(event, current) {
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
    const labels = {
      "run.success": "Analysis complete",
      "run.error": event.payload.message || event.payload.error || "Run failed",
      "run.cancelled": "Run cancelled",
    };
    return {
      activeRunId: current.activeRunId === event.run_id ? null : current.activeRunId,
      status: {
        label: labels[event.type],
        mode: event.type === "run.error" ? "error" : "idle",
      },
    };
  }
  return { activeRunId: current.activeRunId, status: current.status };
}

export function reduceAgentEvent(current, rawEvent) {
  const event = parseAgentEvent(rawEvent);
  if (current.events.some((candidate) => candidate.id === event.id)) return current;
  return {
    events: [...current.events, event],
    ...eventStatus(event, current),
  };
}
