const TERMINAL_RUN_EVENTS = new Set([
  "run.success",
  "run.error",
  "run.cancelled",
  "run.timeout",
]);

function requireString(value, field) {
  if (typeof value !== "string" || value.length === 0) {
    throw new TypeError(`DomainEvent.${field} must be a non-empty string`);
  }
  return value;
}

function finiteNumber(value) {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }
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
  if (rows.length === 0) {
    throw new TypeError("Chart widget requires data rows");
  }

  const labels = rows.map((row, index) =>
    row[widget.x_field] === null || row[widget.x_field] === undefined
      ? String(index + 1)
      : String(row[widget.x_field]),
  );
  const series = widget.y_fields.slice(0, 6).map((field) => ({
    field: String(field),
    values: rows.map((row) => finiteNumber(row[field])),
  }));
  const values = series.flatMap((entry) =>
    entry.values.filter((value) => value !== null),
  );
  if (values.length === 0) {
    throw new TypeError("Chart widget contains no numeric y values");
  }

  const minimum = Math.min(0, ...values);
  let maximum = Math.max(0, ...values);
  if (minimum === maximum) {
    maximum = minimum + 1;
  }
  return {
    kind: widget.kind,
    labels,
    series,
    minimum,
    maximum,
  };
}

export function parseDomainEvent(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError("DomainEvent must be an object");
  }
  if (!Number.isSafeInteger(value.sequence) || value.sequence < 1) {
    throw new TypeError("DomainEvent.sequence must be a positive integer");
  }
  if (!value.payload || typeof value.payload !== "object" || Array.isArray(value.payload)) {
    throw new TypeError("DomainEvent.payload must be an object");
  }
  if (value.run_id !== null && value.run_id !== undefined) {
    requireString(value.run_id, "run_id");
  }
  return {
    ...value,
    id: requireString(value.id, "id"),
    source_event_key: requireString(value.source_event_key, "source_event_key"),
    thread_id: requireString(value.thread_id, "thread_id"),
    type: requireString(value.type, "type"),
    run_id: value.run_id ?? null,
  };
}

export function initialEventProjection() {
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
      "run.timeout": "Run timed out",
    };
    return {
      activeRunId: current.activeRunId === event.run_id ? null : current.activeRunId,
      status: {
        label: labels[event.type],
        mode: event.type === "run.error" ? "error" : "idle",
      },
    };
  }
  return {
    activeRunId: current.activeRunId,
    status: current.status,
  };
}

export function reduceDomainEvent(current, rawEvent) {
  const event = parseDomainEvent(rawEvent);
  if (current.events.some((candidate) => candidate.id === event.id)) {
    return current;
  }
  const events = [...current.events, event].sort(
    (left, right) => left.sequence - right.sequence,
  );
  return { events, ...eventStatus(event, current) };
}

export function replayDomainEvents(events) {
  return [...events]
    .sort((left, right) => left.sequence - right.sequence)
    .reduce(reduceDomainEvent, initialEventProjection());
}
