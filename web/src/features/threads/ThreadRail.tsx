import { useState } from "react";

import type { Thread } from "../../domain/types";
import { CloseIcon, PlusIcon, TrashIcon } from "../../shared/ui/icons";

interface ThreadRailProps {
  activeThreadId?: string;
  onClose: () => void;
  onCreate: () => void;
  onDelete: (thread: Thread) => Promise<void>;
  onSignOut: () => Promise<void>;
  onSelect: (thread: Thread) => void;
  threads: Thread[];
}

export function ThreadRail({
  activeThreadId,
  onClose,
  onCreate,
  onDelete,
  onSignOut,
  onSelect,
  threads,
}: ThreadRailProps) {
  const [confirmingThreadId, setConfirmingThreadId] = useState<string | null>(null);
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);

  async function deleteThread(thread: Thread) {
    setDeletingThreadId(thread.id);
    try {
      await onDelete(thread);
      setConfirmingThreadId(null);
    } catch {
      // The workspace reports the API error and keeps confirmation available to retry.
    } finally {
      setDeletingThreadId(null);
    }
  }

  return (
    <aside className="thread-rail" aria-label="Research navigation">
      <div className="rail-brand-row">
        <h1>LangAlpha</h1>
        <button
          className="icon-button mobile-only"
          type="button"
          onClick={onClose}
          aria-label="Close conversations"
        >
          <CloseIcon />
        </button>
      </div>
      <button className="new-thread" type="button" onClick={onCreate}>
        <PlusIcon />
        <span>New research</span>
      </button>
      <h2>Recent</h2>
      <nav id="thread-list" aria-label="Recent research conversations">
        {threads.map((thread) => {
          const confirming = confirmingThreadId === thread.id;
          const deleting = deletingThreadId === thread.id;
          return (
            <div className="thread-item" key={thread.id}>
              {confirming ? (
                <div
                  className="thread-delete-confirm"
                  role="group"
                  aria-label={`Delete ${thread.title}?`}
                >
                  <span>Delete?</span>
                  <button
                    type="button"
                    disabled={deleting}
                    onClick={() => setConfirmingThreadId(null)}
                  >
                    Cancel
                  </button>
                  <button
                    className="thread-delete-accept"
                    type="button"
                    disabled={deleting}
                    onClick={() => void deleteThread(thread)}
                  >
                    {deleting ? "Deleting…" : "Delete"}
                  </button>
                </div>
              ) : (
                <>
                  <button
                    className={`thread-row${
                      thread.id === activeThreadId ? " active" : ""
                    }`}
                    type="button"
                    title={thread.title}
                    onClick={() => onSelect(thread)}
                  >
                    <span className="thread-title">{thread.title}</span>
                  </button>
                  <button
                    className="thread-delete"
                    type="button"
                    title={`Delete ${thread.title}`}
                    aria-label={`Delete ${thread.title}`}
                    onClick={() => setConfirmingThreadId(thread.id)}
                  >
                    <TrashIcon />
                  </button>
                </>
              )}
            </div>
          );
        })}
      </nav>
      <button
        className="sign-out rail-sign-out"
        type="button"
        onClick={onSignOut}
      >
        Sign out
      </button>
    </aside>
  );
}
