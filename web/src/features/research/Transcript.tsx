import { useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";

import {
  assistantMessageContent,
  formatUsageSummary,
  projectMessages,
} from "../../domain/agent-events";
import {
  citationSegments,
  fileReferenceSegments,
} from "../../domain/message-references";
import type {
  AgentEvent,
  AgentProjection,
  Asset,
  Citation,
  JsonObject,
  RenderSegment,
  Widget,
} from "../../domain/types";
import { WidgetCard } from "./WidgetCard";

function eventText(event: AgentEvent): string {
  if (["message.delta", "message.completed"].includes(event.type)) {
    return assistantMessageContent(event.payload).text;
  }
  if (event.type === "run.error") {
    return String(event.payload.message || event.payload.error || "Run failed");
  }
  if (event.type === "todo.updated") return "Research plan updated";
  if (event.type === "sandbox.bound") return "Daytona workspace ready";
  if (event.type === "asset.ready") {
    return `File ready: ${String(event.payload.filename || "file")}`;
  }
  if (event.type === "widget.ready") {
    const widget = event.payload.widget as JsonObject | undefined;
    return `Widget ready: ${String(widget?.title || event.payload.title || "result")}`;
  }
  if (event.type === "usage.updated") return formatUsageSummary(event.payload);
  return event.type.replaceAll(".", " ");
}

function FriendlyText({
  assets,
  citations,
  text,
}: {
  assets: Asset[];
  citations: Citation[];
  text: string;
}) {
  const nodes: RenderSegment[] = [];
  for (const segment of citationSegments(text, citations)) {
    if (segment.kind === "text") nodes.push(...fileReferenceSegments(segment.value));
    else nodes.push(segment);
  }
  return nodes.map((segment, index) => {
    if (segment.kind === "citation") {
      return (
        <a
          className="inline-citation"
          href={segment.url}
          key={`${segment.url}:${index}`}
          target="_blank"
          rel="noopener noreferrer"
          title={segment.title}
        >
          [{segment.index}]
        </a>
      );
    }
    if (segment.kind === "file") {
      const asset = assets.find((candidate) => candidate.sandbox_path === segment.path);
      return (
        <span className="inline-file" key={`${segment.path}:${index}`} title={segment.path}>
          @{asset?.filename || segment.path.split("/").at(-1) || "file"}
        </span>
      );
    }
    return <span key={index}>{segment.value}</span>;
  });
}

function Activity({
  events,
  projection,
}: {
  events: AgentEvent[];
  projection: AgentProjection;
}) {
  const latest = events.at(-1);
  let label = latest ? eventText(latest) : projection.status.label;
  if (projection.status.mode === "active") {
    label = latest ? `Researching · ${eventText(latest)}` : "Researching…";
  } else if (events.some((event) => event.type === "run.success")) {
    label = "Research complete";
  }
  return (
    <details
      className={`activity${projection.status.mode === "active" ? " running" : ""}${
        projection.status.mode === "error" ? " error" : ""
      }`}
    >
      <summary>{label}</summary>
      <ul className="activity-events">
        {events
          .slice(-8)
          .reverse()
          .map((event) => (
            <li key={event.id}>{eventText(event)}</li>
          ))}
      </ul>
    </details>
  );
}

function interruptValue(event: AgentEvent): Record<string, unknown> {
  const values = event.payload.__interrupt__ || event.payload.interrupts || [];
  const first = Array.isArray(values) ? values[0] : values;
  const raw =
    first && typeof first === "object" && "value" in first
      ? (first as Record<string, unknown>).value
      : first || event.payload;
  return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

function InterruptCard({
  event,
  onResume,
}: {
  event: AgentEvent;
  onResume: (event: AgentEvent, value: unknown) => Promise<void>;
}) {
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const value = interruptValue(event);
  const steps = Array.isArray(value.steps)
    ? value.steps.filter((step): step is Record<string, unknown> =>
        Boolean(step && typeof step === "object"),
      )
    : [];
  const isPlan = value.kind === "plan";

  async function resume(next: unknown) {
    setSubmitting(true);
    try {
      await onResume(event, next);
    } finally {
      setSubmitting(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (answer.trim()) void resume(answer.trim());
  }

  return (
    <section className="interrupt-card">
      <h3>{isPlan ? "Plan approval" : "LangAlpha needs input"}</h3>
      <p>{String(value.question || value.goal || "Review the request before continuing.")}</p>
      {isPlan ? (
        <>
          {steps.length ? (
            <ol>
              {steps.map((step, index) => (
                <li key={index}>
                  {String(step.title || `Step ${index + 1}`)}:{" "}
                  {String(step.description || "")}
                </li>
              ))}
            </ol>
          ) : null}
          <div className="interrupt-actions">
            <button
              className="primary"
              type="button"
              disabled={submitting}
              onClick={() => void resume({ decision: "approve" })}
            >
              Approve
            </button>
            <button
              className="secondary"
              type="button"
              disabled={submitting}
              onClick={() => void resume({ decision: "reject" })}
            >
              Reject
            </button>
          </div>
        </>
      ) : (
        <form className="interrupt-answer" onSubmit={submit}>
          <textarea
            required
            rows={3}
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            placeholder="Your answer…"
          />
          <button className="primary" type="submit" disabled={submitting}>
            Continue
          </button>
        </form>
      )}
    </section>
  );
}

function HtmlArtifact({
  artifact,
  onDownload,
}: {
  artifact: Asset;
  onDownload: (asset: Asset) => Promise<void>;
}) {
  return (
    <section className="widget-card html-artifact-card">
      <div className="widget-heading">
        <h3>{artifact.filename}</h3>
        <span>HTML</span>
      </div>
      <p>Sandbox-generated interactive artifact</p>
      <div className="html-artifact-actions">
        <a
          href={`/api/assets/${artifact.id}/view`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open in new tab
        </a>
        <a
          href="#"
          onClick={(event) => {
            event.preventDefault();
            void onDownload(artifact);
          }}
        >
          Download
        </a>
      </div>
      <iframe
        className="html-artifact-frame"
        src={`/api/assets/${artifact.id}/view`}
        title={`Preview ${artifact.filename}`}
        referrerPolicy="no-referrer"
        sandbox="allow-scripts"
      />
    </section>
  );
}

function eventWidget(event: AgentEvent): Widget {
  const candidate = event.payload.widget;
  return (
    candidate && typeof candidate === "object" && !Array.isArray(candidate)
      ? candidate
      : event.payload
  ) as Widget;
}

interface TranscriptProps {
  assets: Asset[];
  htmlPreview: Asset | null;
  onDownload: (asset: Asset) => Promise<void>;
  onResume: (event: AgentEvent, value: unknown) => Promise<void>;
  projection: AgentProjection;
}

export function Transcript({
  assets,
  htmlPreview,
  onDownload,
  onResume,
  projection,
}: TranscriptProps) {
  const transcriptRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const messages = useMemo(() => projectMessages(projection.events), [projection.events]);
  const progressEvents = useMemo(
    () =>
      projection.events.filter(
        (event) =>
          ![
            "user.message",
            "message.delta",
            "message.completed",
            "widget.ready",
            "interrupt.requested",
            "interrupt.resumed",
          ].includes(event.type),
      ),
    [projection.events],
  );
  const latestProgressRunId = progressEvents.at(-1)?.run_id;
  const latestProgressEvents = latestProgressRunId
    ? progressEvents.filter((event) => event.run_id === latestProgressRunId)
    : progressEvents;
  const unresolved = projection.events.filter(
    (event) =>
      event.type === "interrupt.requested" &&
      !projection.events.some(
        (candidate) =>
          candidate.type === "interrupt.resumed" &&
          candidate.payload.parent_run_id === event.run_id,
      ),
  );

  useLayoutEffect(() => {
    const transcript = transcriptRef.current;
    if (transcript && stickToBottomRef.current) transcript.scrollTop = transcript.scrollHeight;
  }, [htmlPreview, projection.events]);

  if (!projection.events.length && !htmlPreview) {
    return (
      <div className="transcript" ref={transcriptRef} aria-live="polite">
        <div className="empty-state">
          <div className="empty-mark" aria-hidden="true">
            L
          </div>
          <h2>What would you like to research?</h2>
          <p>Ask a question, attach a file, or type @ to reference one already in this workspace.</p>
        </div>
      </div>
    );
  }

  let activityRendered = false;
  return (
    <div
      className="transcript"
      ref={transcriptRef}
      aria-live="polite"
      onScroll={(event) => {
        const element = event.currentTarget;
        stickToBottomRef.current =
          element.scrollHeight - element.scrollTop - element.clientHeight < 140;
      }}
    >
      {messages.map((message) => {
        let activity = null;
        if (
          !activityRendered &&
          progressEvents.length &&
          message.author === "LangAlpha" &&
          message.run_id === latestProgressRunId
        ) {
          activityRendered = true;
          activity = (
            <Activity
              events={latestProgressEvents}
              key={`activity:${latestProgressRunId}`}
              projection={projection}
            />
          );
        }
        const created = new Date(message.created_at).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        });
        return (
          <div key={message.id}>
            {activity}
            <article
              className={`message${message.author === "LangAlpha" ? " assistant" : ""}`}
            >
              <div className="message-head">
                <strong>{message.author}</strong>
                <time>{created}</time>
              </div>
              <div className="message-body">
                <FriendlyText
                  assets={assets}
                  citations={message.citations || []}
                  text={message.text}
                />
              </div>
            </article>
          </div>
        );
      })}
      {!activityRendered && latestProgressEvents.length ? (
        <Activity events={latestProgressEvents} projection={projection} />
      ) : null}
      {projection.events
        .filter((event) => event.type === "widget.ready")
        .map((event) => (
          <WidgetCard key={event.id} widget={eventWidget(event)} />
        ))}
      {htmlPreview ? <HtmlArtifact artifact={htmlPreview} onDownload={onDownload} /> : null}
      {unresolved.length ? (
        <InterruptCard event={unresolved.at(-1)!} onResume={onResume} />
      ) : null}
    </div>
  );
}

export function collectCitations(events: AgentEvent[]): Citation[] {
  const seen = new Set<string>();
  return projectMessages(events).flatMap((message) =>
    (message.citations || []).filter((citation) => {
      try {
        const parsed = new URL(citation.url);
        if (!["http:", "https:"].includes(parsed.protocol)) return false;
      } catch {
        return false;
      }
      if (seen.has(citation.url)) return false;
      seen.add(citation.url);
      return true;
    }),
  );
}
