import { CaretLeftIcon } from "@phosphor-icons/react/dist/icons/CaretLeft";
import { CaretRightIcon } from "@phosphor-icons/react/dist/icons/CaretRight";
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/icons/PencilSimple";
import {
  lazy,
  Suspense,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  projectActivity,
  projectMessages,
} from "../../domain/agent-events";
import {
  citationSegments,
  fileReferenceSegments,
} from "../../domain/message-references";
import type {
  ActivityItem,
  AgentEvent,
  AgentProjection,
  Asset,
  Citation,
  JsonObject,
  RenderSegment,
  ThreadBranchState,
  Widget,
} from "../../domain/types";
import { BrandMark } from "../../shared/ui/BrandMark";
import { WidgetCard } from "./WidgetCard";

const MarkdownMessage = lazy(() => import("./MarkdownMessage"));

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
  const items = projectActivity(events);
  const latest = items.at(-1);
  const visibleItems = items.slice(-16);
  const visuallyComplete = projection.status.label === "Analysis complete";
  const title =
    projection.status.mode === "active"
      ? "Researching"
      : projection.status.mode === "error"
        ? "Research failed"
        : "Research complete";
  let label = latest?.title || projection.status.label;
  if (projection.status.mode === "active") {
    label = latest ? latest.title : "Working through the research plan";
  }
  return (
    <details
      className={`activity${projection.status.mode === "active" ? " running" : ""}${
        projection.status.mode === "error" ? " error" : ""
      }`}
      open={projection.status.mode === "active" ? true : undefined}
    >
      <summary>
        <span className="activity-summary-copy">
          <strong>{title}</strong>
          <span>{label}</span>
        </span>
        {items.length ? (
          <span className="activity-count">
            {items.length} {items.length === 1 ? "step" : "steps"}
          </span>
        ) : null}
      </summary>
      {items.length ? (
        <ol className="activity-events">
          {visibleItems.map((item, index) => (
            <ActivityRow
              item={item}
              key={item.id}
              settled={
                visuallyComplete ||
                (item.kind === "reasoning" && index < visibleItems.length - 1)
              }
            />
          ))}
        </ol>
      ) : (
        <p className="activity-empty">Waiting for the next research step…</p>
      )}
    </details>
  );
}

function ActivityRow({ item, settled }: { item: ActivityItem; settled: boolean }) {
  const created = new Date(item.created_at).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  const expandableReasoning = item.kind === "reasoning" && Boolean(item.detail);
  const status = settled && item.status === "running" ? "complete" : item.status;
  return (
    <li className={`activity-event ${status}${item.kind ? ` ${item.kind}` : ""}`}>
      <span className="activity-event-mark" aria-hidden="true" />
      <div className="activity-event-copy">
        <strong>{item.title}</strong>
        {item.detail ? (
          expandableReasoning ? (
            <details className="activity-reasoning">
              <summary aria-label="Toggle full analysis">
                <span className="activity-reasoning-preview">{item.detail}</span>
              </summary>
            </details>
          ) : (
            <span>{item.detail}</span>
          )
        ) : null}
      </div>
      <time>{created}</time>
    </li>
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
      <h3>LangAlpha needs input</h3>
      <p>{String(value.question || "Please provide the missing information.")}</p>
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
    </section>
  );
}

function LatestUserMessage({
  assets,
  branch,
  disabled,
  onEdit,
  onSelectBranch,
  text,
}: {
  assets: Asset[];
  branch: ThreadBranchState;
  disabled: boolean;
  onEdit: (message: string) => Promise<void>;
  onSelectBranch: (checkpointId: string) => Promise<void>;
  text: string;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const [submitting, setSubmitting] = useState(false);
  const branchCount = branch.options.length;
  const currentIndex = branch.current_index;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const next = draft.trim();
    if (!next || next === text.trim() || submitting) return;
    setSubmitting(true);
    try {
      await onEdit(next);
      setEditing(false);
    } catch {
      // The workspace reports API errors without discarding the draft.
    } finally {
      setSubmitting(false);
    }
  }

  async function selectBranch(index: number) {
    const option = branch.options[index];
    if (!option || submitting) return;
    setSubmitting(true);
    try {
      await onSelectBranch(option.checkpoint_id);
    } catch {
      // The workspace reports API errors and keeps the current branch selected.
    } finally {
      setSubmitting(false);
    }
  }

  if (editing) {
    return (
      <form className="message-edit" onSubmit={(event) => void submit(event)}>
        <textarea
          autoFocus
          rows={3}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-label="Edit latest message"
        />
        <div className="message-edit-actions">
          <button
            type="button"
            onClick={() => {
              setDraft(text);
              setEditing(false);
            }}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            className="primary"
            type="submit"
            disabled={!draft.trim() || draft.trim() === text.trim() || submitting}
          >
            Send
          </button>
        </div>
      </form>
    );
  }

  return (
    <>
      <div className="message-body">
        <FriendlyText assets={assets} citations={[]} text={text} />
      </div>
      <div className="message-actions">
        {branch.can_edit_latest ? (
          <button
            type="button"
            className="message-icon-button"
            aria-label="Edit latest message"
            title="Edit latest message"
            disabled={disabled || submitting}
            onClick={() => setEditing(true)}
          >
            <PencilSimpleIcon aria-hidden="true" />
          </button>
        ) : null}
        {branchCount > 1 ? (
          <div className="message-branches" aria-label="Message branches">
            <button
              type="button"
              aria-label="Previous branch"
              disabled={disabled || submitting || currentIndex <= 0}
              onClick={() => void selectBranch(currentIndex - 1)}
            >
              <CaretLeftIcon aria-hidden="true" />
            </button>
            <span>
              {currentIndex + 1} / {branchCount}
            </span>
            <button
              type="button"
              aria-label="Next branch"
              disabled={disabled || submitting || currentIndex >= branchCount - 1}
              onClick={() => void selectBranch(currentIndex + 1)}
            >
              <CaretRightIcon aria-hidden="true" />
            </button>
          </div>
        ) : null}
      </div>
    </>
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
  branch: ThreadBranchState;
  onEditLatest: (message: string) => Promise<void>;
  onResume: (event: AgentEvent, value: unknown) => Promise<void>;
  onSelectBranch: (checkpointId: string) => Promise<void>;
  projection: AgentProjection;
}

export function Transcript({
  assets,
  branch,
  onEditLatest,
  onResume,
  onSelectBranch,
  projection,
}: TranscriptProps) {
  const transcriptRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const messages = useMemo(() => projectMessages(projection.events), [projection.events]);
  const latestUserMessageId = messages.findLast(
    (message) => message.author === "You",
  )?.id;
  const progressEvents = useMemo(
    () =>
      projection.events.filter(
        (event) =>
          projectActivity([event]).length > 0 ||
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
  }, [projection.events]);

  if (!projection.events.length) {
    return (
      <div className="transcript" ref={transcriptRef} aria-live="polite">
        <div className="empty-state">
          <BrandMark className="empty-brand-mark" />
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
              {message.author === "You" && message.id === latestUserMessageId ? (
                <LatestUserMessage
                  assets={assets}
                  branch={branch}
                  disabled={Boolean(projection.activeRunId)}
                  key={branch.current_checkpoint_id || message.id}
                  onEdit={onEditLatest}
                  onSelectBranch={onSelectBranch}
                  text={message.text}
                />
              ) : (
                <div className="message-body">
                  {message.author === "LangAlpha" ? (
                  <Suspense
                    fallback={
                      <FriendlyText
                        assets={assets}
                        citations={message.citations || []}
                        text={message.text}
                      />
                    }
                  >
                    <MarkdownMessage
                      assets={assets}
                      citations={message.citations || []}
                      text={message.text}
                    />
                  </Suspense>
                  ) : (
                    <FriendlyText
                      assets={assets}
                      citations={message.citations || []}
                      text={message.text}
                    />
                  )}
                </div>
              )}
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
