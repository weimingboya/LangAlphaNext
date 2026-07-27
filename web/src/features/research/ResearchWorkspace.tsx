import { useMemo } from "react";

import { projectTodos } from "../../domain/agent-events";
import type { ApiClient } from "../../shared/api/api-client";
import { ContextPanel } from "../assets/ContextPanel";
import { ThreadRail } from "../threads/ThreadRail";
import { Composer } from "./Composer";
import { ConversationHeader } from "./ConversationHeader";
import { collectCitations, Transcript } from "./Transcript";
import { useResearchWorkspace } from "./useResearchWorkspace";

interface ResearchWorkspaceProps {
  client: ApiClient;
  onSignOut: () => Promise<void>;
}

export function ResearchWorkspace({ client, onSignOut }: ResearchWorkspaceProps) {
  const workspace = useResearchWorkspace(client);
  const citations = useMemo(
    () => collectCitations(workspace.projection.events),
    [workspace.projection.events],
  );
  const todos = useMemo(
    () => projectTodos(workspace.projection.events),
    [workspace.projection.events],
  );
  const className = [
    "app-shell",
    workspace.contextOpen ? "context-open" : "",
    workspace.threadDrawerOpen ? "threads-open" : "",
  ]
    .filter(Boolean)
    .join(" ");

  function report(reason: unknown) {
    workspace.notify(reason instanceof Error ? reason.message : String(reason));
  }

  return (
    <main className={className}>
      <ThreadRail
        activeThreadId={workspace.activeThread?.id}
        onClose={() => workspace.setThreadDrawerOpen(false)}
        onCreate={() => {
          workspace.setThreadDrawerOpen(false);
          void workspace.createThread().catch(report);
        }}
        onDelete={workspace.deleteThread}
        onSignOut={onSignOut}
        onSelect={(thread) => void workspace.selectThread(thread).catch(report)}
        threads={workspace.threads}
      />

      <section className="research-pane" aria-label="Research conversation">
        <ConversationHeader
          assetCount={workspace.assets.length}
          contextOpen={workspace.contextOpen}
          onOpenThreads={() => {
            workspace.setContextOpen(false);
            workspace.setThreadDrawerOpen(true);
          }}
          onToggleContext={() => {
            workspace.setThreadDrawerOpen(false);
            workspace.setContextOpen(!workspace.contextOpen);
          }}
          title={workspace.activeThread?.title || "New research"}
        />

        {workspace.initializeError ? (
          <div className="transcript">
            <div className="empty-state">
              <div className="empty-mark" aria-hidden="true">
                !
              </div>
              <h2>Workspace unavailable</h2>
              <p>{workspace.initializeError}</p>
            </div>
          </div>
        ) : workspace.loading ? (
          <div className="transcript">
            <div className="empty-state">
              <div className="empty-mark loading-mark" aria-hidden="true">
                L
              </div>
              <h2>Opening your workspace…</h2>
            </div>
          </div>
        ) : (
          <Transcript
            assets={workspace.assets}
            htmlPreview={workspace.htmlPreview}
            onDownload={workspace.downloadAsset}
            onResume={workspace.resumeInterrupt}
            projection={workspace.projection}
          />
        )}

        <Composer
          activeRunId={workspace.projection.activeRunId}
          assets={workspace.assets}
          filePickerOpen={workspace.filePickerOpen}
          fileQuery={workspace.fileQuery}
          notify={workspace.notify}
          onCancel={workspace.cancelRun}
          onFilePickerOpen={workspace.openFilePicker}
          onFilePickerToggle={workspace.setFilePickerOpen}
          onFileQueryChange={workspace.setFileQuery}
          onReferenceChange={workspace.selectReference}
          onSubmit={workspace.submitMessage}
          onUpload={workspace.uploadFile}
          selectedReference={workspace.selectedReference}
        />
      </section>

      <ContextPanel
        assets={workspace.assets}
        citations={citations}
        onClose={() => workspace.setContextOpen(false)}
        onDownload={(asset) => void workspace.downloadAsset(asset).catch(report)}
        onOpenHtml={(asset) => {
          workspace.setHtmlPreview(asset);
          workspace.setContextOpen(false);
        }}
        onReference={() => workspace.openFilePicker()}
        todos={todos}
      />

      <button
        className="drawer-backdrop"
        type="button"
        onClick={workspace.closeDrawers}
        aria-label="Close open panel"
      />
      {workspace.toast ? (
        <div className="toast" role="status" aria-live="polite">
          {workspace.toast}
        </div>
      ) : null}
    </main>
  );
}
