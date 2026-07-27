import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import { initialAgentProjection } from "../../domain/agent-events";
import { serializeFileReference } from "../../domain/message-references";
import {
  isDefaultThreadTitle,
  threadTitleFromMessage,
} from "../../domain/thread-title";
import type {
  AgentEvent,
  AgentProjection,
  Asset,
  AssetDownloadTicket,
  Run,
  Thread,
  ThreadSnapshot,
} from "../../domain/types";
import type { ApiClient } from "../../shared/api/api-client";
import { uploadAsset } from "../assets/upload";
import {
  asPayload,
  isAsset,
  projectionReducer,
  snapshotEvent,
} from "./workspace-events";

export interface ResearchWorkspaceState {
  activeThread: Thread | null;
  assets: Asset[];
  cancelRun: () => Promise<void>;
  closeDrawers: () => void;
  connectRun: (threadId: string, runId: string) => void;
  contextOpen: boolean;
  createThread: () => Promise<Thread>;
  deleteThread: (thread: Thread) => Promise<void>;
  downloadAsset: (asset: Asset) => Promise<void>;
  filePickerOpen: boolean;
  fileQuery: string;
  htmlPreview: Asset | null;
  initializeError: string | null;
  interruptRun: (message: string) => Promise<void>;
  loading: boolean;
  notify: (message: string) => void;
  openFilePicker: (query?: string) => void;
  projection: AgentProjection;
  resumeInterrupt: (event: AgentEvent, value: unknown) => Promise<void>;
  selectReference: (asset: Asset | null) => void;
  selectedReference: Asset | null;
  selectThread: (thread: Thread) => Promise<void>;
  setContextOpen: (open: boolean) => void;
  setFilePickerOpen: (open: boolean) => void;
  setFileQuery: (query: string) => void;
  setHtmlPreview: (asset: Asset | null) => void;
  setThreadDrawerOpen: (open: boolean) => void;
  submitMessage: (message: string, strategy?: "enqueue" | "interrupt") => Promise<void>;
  threadDrawerOpen: boolean;
  threads: Thread[];
  toast: string | null;
  uploadFile: (file: File) => Promise<Asset>;
}

export function useResearchWorkspace(client: ApiClient): ResearchWorkspaceState {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeThread, setActiveThread] = useState<Thread | null>(null);
  const [projection, dispatchProjection] = useReducer(
    projectionReducer,
    undefined,
    initialAgentProjection,
  );
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedReference, setSelectedReference] = useState<Asset | null>(null);
  const [htmlPreview, setHtmlPreview] = useState<Asset | null>(null);
  const [contextOpen, setContextOpen] = useState(() => window.innerWidth > 1180);
  const [threadDrawerOpen, setThreadDrawerOpen] = useState(false);
  const [filePickerOpen, setFilePickerOpen] = useState(false);
  const [fileQuery, setFileQuery] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [initializeError, setInitializeError] = useState<string | null>(null);

  const sourceRef = useRef<EventSource | null>(null);
  const activeThreadRef = useRef<Thread | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const snapshotRequestIdRef = useRef(0);
  const toastTimerRef = useRef<number | null>(null);
  const loadSnapshotRef = useRef<(threadId: string, requestId: number) => Promise<void>>(
    async () => undefined,
  );

  useEffect(() => {
    activeThreadRef.current = activeThread;
  }, [activeThread]);

  useEffect(() => {
    activeRunIdRef.current = projection.activeRunId;
  }, [projection.activeRunId]);

  const notify = useCallback((message: string) => {
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
    setToast(message);
    toastTimerRef.current = window.setTimeout(() => setToast(null), 3200);
  }, []);

  const addAsset = useCallback((asset: Asset) => {
    setAssets((current) => {
      const index = current.findIndex((candidate) => candidate.id === asset.id);
      if (index < 0) return [...current, asset];
      return current.map((candidate) => (candidate.id === asset.id ? asset : candidate));
    });
  }, []);

  const recordEvent = useCallback(
    (event: AgentEvent) => {
      dispatchProjection({ type: "record", event });
      if (event.type === "run.error") {
        const message = event.payload.message || event.payload.error || "Run failed";
        notify(String(message));
      }
    },
    [notify],
  );

  const connectRun = useCallback(
    (threadId: string, runId: string) => {
      sourceRef.current?.close();
      const source = new EventSource(`/api/threads/${threadId}/runs/${runId}/stream`);
      sourceRef.current = source;
      const types = [
        "run.success",
        "run.error",
        "run.interrupted",
        "run.cancelled",
        "message.delta",
        "message.completed",
        "activity.updated",
        "todo.updated",
        "sandbox.bound",
        "asset.ready",
        "asset.failed",
        "widget.ready",
        "interrupt.requested",
      ];
      for (const type of types) {
        source.addEventListener(type, (incoming) => {
          try {
            const event = JSON.parse((incoming as MessageEvent<string>).data) as AgentEvent;
            recordEvent(event);
            if (type === "asset.ready" && isAsset(event.payload)) addAsset(event.payload);
            if (type.startsWith("run.")) {
              source.close();
              if (sourceRef.current === source) sourceRef.current = null;
              void loadSnapshotRef
                .current(threadId, snapshotRequestIdRef.current)
                .catch((reason: unknown) =>
                  notify(reason instanceof Error ? reason.message : String(reason)),
                );
            }
          } catch (reason) {
            notify(reason instanceof Error ? reason.message : String(reason));
          }
        });
      }
      source.onerror = () => {
        if (source.readyState === EventSource.CLOSED) return;
        notify("The live research stream was interrupted. Reconnecting…");
      };
    },
    [addAsset, notify, recordEvent],
  );

  const loadSnapshot = useCallback(
    async (threadId: string, requestId: number) => {
      const snapshot = await client.request<ThreadSnapshot>(
        `/api/threads/${threadId}/snapshot`,
      );
      if (
        requestId !== snapshotRequestIdRef.current ||
        activeThreadRef.current?.id !== threadId
      ) {
        return;
      }

      dispatchProjection({ type: "reset" });
      const fallbackRunId = snapshot.runs[0]?.id || `snapshot:${threadId}`;
      const turnRuns = snapshot.runs
        .filter((run) => !run.parent_run_id)
        .toReversed();
      let turnIndex = -1;
      let messageRunId = turnRuns[0]?.id || fallbackRunId;
      for (const message of snapshot.messages) {
        if (message.role === "user") {
          turnIndex += 1;
          messageRunId = turnRuns[turnIndex]?.id || messageRunId;
          recordEvent(
            snapshotEvent(
              threadId,
              messageRunId,
              "user.message",
              { content: message.content ?? "" },
              `snapshot:message:${String(message.id)}`,
            ),
          );
        } else if (["assistant", "tool"].includes(String(message.role))) {
          recordEvent(
            snapshotEvent(
              threadId,
              messageRunId,
              "message.completed",
              message,
              `snapshot:message:${String(message.id)}`,
            ),
          );
        }
      }
      for (const activity of snapshot.activities || []) {
        recordEvent(activity);
      }
      for (const widget of snapshot.widgets) {
        recordEvent(
          snapshotEvent(
            threadId,
            fallbackRunId,
            "widget.ready",
            { widget: asPayload(widget) },
            `snapshot:widget:${String(widget.id)}`,
          ),
        );
      }
      if (snapshot.todos.length) {
        recordEvent(
          snapshotEvent(
            threadId,
            fallbackRunId,
            "todo.updated",
            asPayload({ todos: snapshot.todos }),
            `snapshot:todos:${threadId}`,
          ),
        );
      }
      if (snapshot.usage.total_tokens || snapshot.usage.web_search_calls) {
        recordEvent(
          snapshotEvent(
            threadId,
            fallbackRunId,
            "usage.updated",
            asPayload(snapshot.usage),
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
      setAssets(snapshot.assets);

      const active = snapshot.runs.find((run) =>
        ["pending", "running"].includes(run.status),
      );
      if (active) {
        recordEvent(
          snapshotEvent(
            threadId,
            active.id,
            "run.started",
            asPayload(active),
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
            asPayload(latest),
            `snapshot:terminal:${latest.id}:${latest.status}`,
          ),
        );
      }
    },
    [client, connectRun, recordEvent],
  );
  loadSnapshotRef.current = loadSnapshot;

  const selectThread = useCallback(
    async (thread: Thread) => {
      const requestId = snapshotRequestIdRef.current + 1;
      snapshotRequestIdRef.current = requestId;
      activeThreadRef.current = thread;
      activeRunIdRef.current = null;
      setActiveThread(thread);
      setAssets([]);
      setSelectedReference(null);
      setHtmlPreview(null);
      setThreadDrawerOpen(false);
      sourceRef.current?.close();
      sourceRef.current = null;
      dispatchProjection({ type: "reset" });
      await loadSnapshot(thread.id, requestId);
    },
    [loadSnapshot],
  );

  const applyThreadUpdate = useCallback((updated: Thread) => {
    setThreads((current) =>
      current.map((thread) => (thread.id === updated.id ? updated : thread)),
    );
    if (activeThreadRef.current?.id === updated.id) {
      activeThreadRef.current = updated;
      setActiveThread(updated);
    }
  }, []);

  const createThread = useCallback(async (): Promise<Thread> => {
    const thread = await client.request<Thread>("/api/threads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "New research" }),
    });
    setThreads((current) => [thread, ...current]);
    await selectThread(thread);
    return thread;
  }, [client, selectThread]);

  const ensureThread = useCallback(async (): Promise<Thread> => {
    return activeThreadRef.current || createThread();
  }, [createThread]);

  const deleteThread = useCallback(
    async (thread: Thread) => {
      try {
        await client.request<void>(`/api/threads/${thread.id}`, {
          method: "DELETE",
        });
      } catch (reason) {
        notify(reason instanceof Error ? reason.message : String(reason));
        throw reason;
      }

      const remaining = threads.filter((candidate) => candidate.id !== thread.id);
      setThreads(remaining);

      if (activeThreadRef.current?.id === thread.id) {
        snapshotRequestIdRef.current += 1;
        sourceRef.current?.close();
        sourceRef.current = null;
        activeThreadRef.current = null;
        activeRunIdRef.current = null;
        setActiveThread(null);
        setAssets([]);
        setSelectedReference(null);
        setHtmlPreview(null);
        setFilePickerOpen(false);
        dispatchProjection({ type: "reset" });

        if (remaining.length) await selectThread(remaining[0]);
      }

      notify("Research deleted");
    },
    [client, notify, selectThread, threads],
  );

  const submitMessage = useCallback(
    async (rawMessage: string, strategy: "enqueue" | "interrupt" = "enqueue") => {
      const message = serializeFileReference(rawMessage, selectedReference);
      if (!message) return;
      const thread = await ensureThread();
      const wasActive = Boolean(activeRunIdRef.current);
      const suggestedTitle = isDefaultThreadTitle(thread.title)
        ? threadTitleFromMessage(rawMessage)
        : null;
      const renamePromise: Promise<Thread | null> = suggestedTitle
        ? client
            .request<Thread>(`/api/threads/${thread.id}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ title: suggestedTitle }),
            })
            .catch((reason: unknown) => {
              notify(
                `Thread name could not be updated: ${
                  reason instanceof Error ? reason.message : String(reason)
                }`,
              );
              return null;
            })
        : Promise.resolve(null);
      const [run, renamedThread] = await Promise.all([
        client.request<Run>(`/api/threads/${thread.id}/runs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message,
            strategy,
            input_asset_ids: selectedReference ? [selectedReference.id] : [],
          }),
        }),
        renamePromise,
      ]);
      if (renamedThread) applyThreadUpdate(renamedThread);
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
        snapshotEvent(
          thread.id,
          run.id,
          "run.started",
          asPayload(run),
          `local:run:${run.id}`,
        ),
      );
      setSelectedReference(null);
      setFilePickerOpen(false);
      setFileQuery("");
      if (!wasActive) connectRun(thread.id, run.id);
    },
    [
      applyThreadUpdate,
      client,
      connectRun,
      ensureThread,
      notify,
      recordEvent,
      selectedReference,
    ],
  );

  const interruptRun = useCallback(
    (message: string) => submitMessage(message, "interrupt"),
    [submitMessage],
  );

  const resumeInterrupt = useCallback(
    async (event: AgentEvent, value: unknown) => {
      const thread = activeThreadRef.current;
      if (!thread) return;
      const run = await client.request<Run>(
        `/api/threads/${thread.id}/runs/${event.run_id}/resume`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value }),
        },
      );
      recordEvent(
        snapshotEvent(
          thread.id,
          run.id,
          "interrupt.resumed",
          { parent_run_id: event.run_id },
          `local:interrupt-resumed:${run.id}`,
        ),
      );
      recordEvent(
        snapshotEvent(
          thread.id,
          run.id,
          "run.started",
          asPayload(run),
          `local:run:${run.id}`,
        ),
      );
      connectRun(thread.id, run.id);
    },
    [client, connectRun, recordEvent],
  );

  const cancelRun = useCallback(async () => {
    const thread = activeThreadRef.current;
    const runId = activeRunIdRef.current;
    if (!thread || !runId) return;
    sourceRef.current?.close();
    sourceRef.current = null;
    try {
      await client.request<void>(`/api/threads/${thread.id}/runs/${runId}/cancel`, {
        method: "POST",
      });
    } catch (reason) {
      connectRun(thread.id, runId);
      throw reason;
    }
    recordEvent(
      snapshotEvent(
        thread.id,
        runId,
        "run.cancelled",
        {},
        `local:run-cancelled:${runId}`,
      ),
    );
  }, [client, connectRun, recordEvent]);

  const uploadFile = useCallback(
    async (file: File): Promise<Asset> => {
      const thread = await ensureThread();
      const asset = await uploadAsset(client, thread, file);
      addAsset(asset);
      setSelectedReference(asset);
      setFilePickerOpen(false);
      setFileQuery("");
      notify(`${asset.filename} uploaded and referenced`);
      return asset;
    },
    [addAsset, client, ensureThread, notify],
  );

  const downloadAsset = useCallback(
    async (asset: Asset) => {
      const ticket = await client.request<AssetDownloadTicket>(
        `/api/assets/${asset.id}/download-url`,
        { method: "POST" },
      );
      window.open(ticket.url, "_blank", "noopener,noreferrer");
    },
    [client],
  );

  const openFilePicker = useCallback((query = "") => {
    setFileQuery(query);
    setFilePickerOpen(true);
  }, []);

  const closeDrawers = useCallback(() => {
    setThreadDrawerOpen(false);
    if (window.innerWidth <= 1180) setContextOpen(false);
  }, []);

  useEffect(() => {
    let active = true;
    client
      .request<Thread[]>("/api/threads")
      .then(async (items) => {
        if (!active) return;
        setThreads(items);
        if (items.length) await selectThread(items[0]);
      })
      .catch((reason: unknown) => {
        if (active) {
          setInitializeError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      sourceRef.current?.close();
    };
  }, [client, selectThread]);

  useEffect(
    () => () => {
      sourceRef.current?.close();
      if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
    },
    [],
  );

  return useMemo(
    () => ({
      activeThread,
      assets,
      cancelRun,
      closeDrawers,
      connectRun,
      contextOpen,
      createThread,
      deleteThread,
      downloadAsset,
      filePickerOpen,
      fileQuery,
      htmlPreview,
      initializeError,
      interruptRun,
      loading,
      notify,
      openFilePicker,
      projection,
      resumeInterrupt,
      selectReference: setSelectedReference,
      selectedReference,
      selectThread,
      setContextOpen,
      setFilePickerOpen,
      setFileQuery,
      setHtmlPreview,
      setThreadDrawerOpen,
      submitMessage,
      threadDrawerOpen,
      threads,
      toast,
      uploadFile,
    }),
    [
      activeThread,
      assets,
      cancelRun,
      closeDrawers,
      connectRun,
      contextOpen,
      createThread,
      deleteThread,
      downloadAsset,
      filePickerOpen,
      fileQuery,
      htmlPreview,
      initializeError,
      interruptRun,
      loading,
      notify,
      openFilePicker,
      projection,
      resumeInterrupt,
      selectedReference,
      selectThread,
      submitMessage,
      threadDrawerOpen,
      threads,
      toast,
      uploadFile,
    ],
  );
}
