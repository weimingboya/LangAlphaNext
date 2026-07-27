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
  Project,
  Run,
  Thread,
  ThreadBranchState,
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
  activeProject: Project | null;
  activeThread: Thread | null;
  assets: Asset[];
  branch: ThreadBranchState;
  cancelRun: () => Promise<void>;
  closeDrawers: () => void;
  connectRun: (threadId: string, runId: string) => void;
  contextOpen: boolean;
  createProject: (name: string) => Promise<Project>;
  createThread: () => Promise<Thread>;
  deleteProject: (project: Project) => Promise<void>;
  deleteThread: (thread: Thread) => Promise<void>;
  downloadAsset: (asset: Asset) => Promise<void>;
  editLatestMessage: (message: string) => Promise<void>;
  filePickerOpen: boolean;
  fileQuery: string;
  htmlPreview: Asset | null;
  initializeError: string | null;
  interruptRun: (message: string) => Promise<void>;
  loading: boolean;
  notify: (message: string) => void;
  openFilePicker: (query?: string) => void;
  projection: AgentProjection;
  projects: Project[];
  renameProject: (project: Project, name: string) => Promise<Project>;
  resumeInterrupt: (event: AgentEvent, value: unknown) => Promise<void>;
  selectProject: (project: Project) => Promise<void>;
  selectBranch: (checkpointId: string) => Promise<void>;
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

const emptyBranchState = (): ThreadBranchState => ({
  current_checkpoint_id: null,
  current_index: 0,
  options: [],
  can_edit_latest: false,
});

export function useResearchWorkspace(client: ApiClient): ResearchWorkspaceState {
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProject, setActiveProject] = useState<Project | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeThread, setActiveThread] = useState<Thread | null>(null);
  const [projection, dispatchProjection] = useReducer(
    projectionReducer,
    undefined,
    initialAgentProjection,
  );
  const [assets, setAssets] = useState<Asset[]>([]);
  const [branch, setBranch] = useState<ThreadBranchState>(emptyBranchState);
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
  const activeProjectRef = useRef<Project | null>(null);
  const activeThreadRef = useRef<Thread | null>(null);
  const activeRunIdRef = useRef<string | null>(null);
  const branchRef = useRef<ThreadBranchState>(branch);
  const snapshotRequestIdRef = useRef(0);
  const toastTimerRef = useRef<number | null>(null);
  const loadSnapshotRef = useRef<
    (threadId: string, requestId: number, checkpointId?: string) => Promise<void>
  >(async () => undefined);

  useEffect(() => {
    activeProjectRef.current = activeProject;
  }, [activeProject]);

  useEffect(() => {
    activeThreadRef.current = activeThread;
  }, [activeThread]);

  useEffect(() => {
    activeRunIdRef.current = projection.activeRunId;
  }, [projection.activeRunId]);

  useEffect(() => {
    branchRef.current = branch;
  }, [branch]);

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
      let refreshing = false;
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
        if (source.readyState === EventSource.CLOSED || refreshing) return;
        refreshing = true;
        notify("The live research stream was interrupted. Reconnecting…");
        void client
          .request<{ id: string }>("/api/auth/me")
          .then(() => {
            source.close();
            if (sourceRef.current === source) sourceRef.current = null;
            return loadSnapshotRef.current(threadId, snapshotRequestIdRef.current);
          })
          .catch((reason: unknown) => {
            notify(reason instanceof Error ? reason.message : String(reason));
          })
          .finally(() => {
            refreshing = false;
          });
      };
    },
    [addAsset, client, notify, recordEvent],
  );

  const loadSnapshot = useCallback(
    async (threadId: string, requestId: number, checkpointId?: string) => {
      const checkpointQuery = checkpointId
        ? `?checkpoint_id=${encodeURIComponent(checkpointId)}`
        : "";
      const snapshot = await client.request<ThreadSnapshot>(
        `/api/threads/${threadId}/snapshot${checkpointQuery}`,
      );
      if (
        requestId !== snapshotRequestIdRef.current ||
        activeThreadRef.current?.id !== threadId
      ) {
        return;
      }

      dispatchProjection({ type: "reset" });
      branchRef.current = snapshot.branch;
      setBranch(snapshot.branch);
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
      setAssets(snapshot.assets.filter((asset) => asset.status === "ready"));

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
      branchRef.current = emptyBranchState();
      setBranch(emptyBranchState());
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

  const selectProject = useCallback(
    async (project: Project) => {
      snapshotRequestIdRef.current += 1;
      sourceRef.current?.close();
      sourceRef.current = null;
      activeProjectRef.current = project;
      activeThreadRef.current = null;
      activeRunIdRef.current = null;
      setActiveProject(project);
      setActiveThread(null);
      setThreads([]);
      setAssets([]);
      branchRef.current = emptyBranchState();
      setBranch(emptyBranchState());
      setSelectedReference(null);
      setHtmlPreview(null);
      setFilePickerOpen(false);
      dispatchProjection({ type: "reset" });

      const [projectThreads, projectAssets] = await Promise.all([
        client.request<Thread[]>(`/api/projects/${project.id}/threads`),
        client.request<Asset[]>(`/api/projects/${project.id}/assets`),
      ]);
      if (activeProjectRef.current?.id !== project.id) return;
      setThreads(projectThreads);
      setAssets(projectAssets.filter((asset) => asset.status === "ready"));
      if (projectThreads.length) await selectThread(projectThreads[0]);
    },
    [client, selectThread],
  );

  const createProject = useCallback(
    async (name: string): Promise<Project> => {
      const project = await client.request<Project>("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      setProjects((current) => [project, ...current]);
      await selectProject(project);
      return project;
    },
    [client, selectProject],
  );

  const ensureProject = useCallback(async (): Promise<Project> => {
    return activeProjectRef.current || createProject("My Research");
  }, [createProject]);

  const createThread = useCallback(async (): Promise<Thread> => {
    const project = await ensureProject();
    const thread = await client.request<Thread>(
      `/api/projects/${project.id}/threads`,
      {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "New research" }),
      },
    );
    setThreads((current) => [thread, ...current]);
    await selectThread(thread);
    return thread;
  }, [client, ensureProject, selectThread]);

  const renameProject = useCallback(
    async (project: Project, name: string): Promise<Project> => {
      const updated = await client.request<Project>(`/api/projects/${project.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      setProjects((current) =>
        current.map((candidate) => (candidate.id === updated.id ? updated : candidate)),
      );
      if (activeProjectRef.current?.id === updated.id) {
        activeProjectRef.current = updated;
        setActiveProject(updated);
      }
      return updated;
    },
    [client],
  );

  const deleteProject = useCallback(
    async (project: Project) => {
      await client.request<void>(`/api/projects/${project.id}`, { method: "DELETE" });
      const remaining = projects.filter((candidate) => candidate.id !== project.id);
      setProjects(remaining);
      if (activeProjectRef.current?.id === project.id) {
        activeProjectRef.current = null;
        setActiveProject(null);
        setThreads([]);
        setActiveThread(null);
        setAssets([]);
        dispatchProjection({ type: "reset" });
        if (remaining.length) await selectProject(remaining[0]);
      }
      notify("Project deleted");
    },
    [client, notify, projects, selectProject],
  );

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
        branchRef.current = emptyBranchState();
        setBranch(emptyBranchState());
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
            branch_checkpoint_id: wasActive
              ? null
              : branchRef.current.current_checkpoint_id,
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

  const selectBranch = useCallback(
    async (checkpointId: string) => {
      const thread = activeThreadRef.current;
      if (!thread || checkpointId === branchRef.current.current_checkpoint_id) return;
      if (activeRunIdRef.current) {
        throw new Error("Wait for the current response before switching branches.");
      }
      const requestId = snapshotRequestIdRef.current + 1;
      snapshotRequestIdRef.current = requestId;
      sourceRef.current?.close();
      sourceRef.current = null;
      await loadSnapshot(thread.id, requestId, checkpointId);
    },
    [loadSnapshot],
  );

  const editLatestMessage = useCallback(
    async (rawMessage: string) => {
      const thread = activeThreadRef.current;
      const checkpointId = branchRef.current.current_checkpoint_id;
      const message = rawMessage.trim();
      if (!thread || !checkpointId || !message) return;
      if (activeRunIdRef.current) {
        throw new Error("Wait for the current response before editing your message.");
      }
      await client.request<Run>(`/api/threads/${thread.id}/runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          strategy: "enqueue",
          input_asset_ids: [],
          branch_checkpoint_id: checkpointId,
          edit_latest: true,
        }),
      });
      const requestId = snapshotRequestIdRef.current + 1;
      snapshotRequestIdRef.current = requestId;
      sourceRef.current?.close();
      sourceRef.current = null;
      await loadSnapshot(thread.id, requestId);
    },
    [client, loadSnapshot],
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
      const project = await ensureProject();
      const asset = await uploadAsset(client, project, file);
      addAsset(asset);
      setSelectedReference(asset);
      setFilePickerOpen(false);
      setFileQuery("");
      notify(`${asset.filename} uploaded and referenced`);
      return asset;
    },
    [addAsset, client, ensureProject, notify],
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
      .request<Project[]>("/api/projects")
      .then(async (items) => {
        if (!active) return;
        let available = items;
        if (!available.length) {
          const created = await client.request<Project>("/api/projects", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: "My Research" }),
          });
          available = [created];
        }
        if (!active) return;
        setProjects(available);
        await selectProject(available[0]);
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
  }, [client, selectProject]);

  useEffect(
    () => () => {
      sourceRef.current?.close();
      if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
    },
    [],
  );

  return useMemo(
    () => ({
      activeProject,
      activeThread,
      assets,
      branch,
      cancelRun,
      closeDrawers,
      connectRun,
      contextOpen,
      createProject,
      createThread,
      deleteProject,
      deleteThread,
      downloadAsset,
      editLatestMessage,
      filePickerOpen,
      fileQuery,
      htmlPreview,
      initializeError,
      interruptRun,
      loading,
      notify,
      openFilePicker,
      projection,
      projects,
      renameProject,
      resumeInterrupt,
      selectBranch,
      selectProject,
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
      activeProject,
      activeThread,
      assets,
      branch,
      cancelRun,
      closeDrawers,
      connectRun,
      contextOpen,
      createProject,
      createThread,
      deleteProject,
      deleteThread,
      downloadAsset,
      editLatestMessage,
      filePickerOpen,
      fileQuery,
      htmlPreview,
      initializeError,
      interruptRun,
      loading,
      notify,
      openFilePicker,
      projection,
      projects,
      renameProject,
      resumeInterrupt,
      selectBranch,
      selectProject,
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
