import { BriefcaseIcon } from "@phosphor-icons/react/dist/icons/Briefcase";
import { CaretDownIcon } from "@phosphor-icons/react/dist/icons/CaretDown";
import { CheckIcon } from "@phosphor-icons/react/dist/icons/Check";
import { FileTextIcon } from "@phosphor-icons/react/dist/icons/FileText";
import { FolderIcon } from "@phosphor-icons/react/dist/icons/Folder";
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/icons/PencilSimple";
import { PlusIcon } from "@phosphor-icons/react/dist/icons/Plus";
import { SignOutIcon } from "@phosphor-icons/react/dist/icons/SignOut";
import { TrashIcon } from "@phosphor-icons/react/dist/icons/Trash";
import { XIcon } from "@phosphor-icons/react/dist/icons/X";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useEffect, useRef, useState } from "react";

import type { Project, Thread } from "../../domain/types";

interface ThreadRailProps {
  activeProject: Project | null;
  activeThreadId?: string;
  onClose: () => void;
  onCreate: () => void;
  onCreateProject: (name: string) => Promise<void>;
  onDelete: (thread: Thread) => Promise<void>;
  onDeleteProject: (project: Project) => Promise<void>;
  onRenameProject: (project: Project, name: string) => Promise<void>;
  onSelectProject: (project: Project) => Promise<void>;
  onSignOut: () => Promise<void>;
  onSelect: (thread: Thread) => void;
  projects: Project[];
  threads: Thread[];
}

type ProjectAction = "create" | "rename" | "delete" | null;

export function ThreadRail({
  activeProject,
  activeThreadId,
  onClose,
  onCreate,
  onCreateProject,
  onDelete,
  onDeleteProject,
  onRenameProject,
  onSelectProject,
  onSignOut,
  onSelect,
  projects,
  threads,
}: ThreadRailProps) {
  const [confirmingThreadId, setConfirmingThreadId] = useState<string | null>(null);
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [projectAction, setProjectAction] = useState<ProjectAction>(null);
  const [projectName, setProjectName] = useState("");
  const [projectPending, setProjectPending] = useState(false);
  const projectControlRef = useRef<HTMLDivElement>(null);
  const projectInputRef = useRef<HTMLInputElement>(null);
  const projectMenuRef = useRef<HTMLDivElement>(null);
  const projectTriggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!projectMenuOpen) return;

    function handlePointerDown(event: PointerEvent) {
      if (!projectControlRef.current?.contains(event.target as Node)) {
        setProjectMenuOpen(false);
        setProjectAction(null);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (projectAction) {
        setProjectAction(null);
      } else {
        setProjectMenuOpen(false);
        projectTriggerRef.current?.focus();
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [projectAction, projectMenuOpen]);

  useEffect(() => {
    if (projectAction === "create" || projectAction === "rename") {
      projectInputRef.current?.focus();
      projectInputRef.current?.select();
    }
  }, [projectAction]);

  function closeProjectMenu() {
    setProjectMenuOpen(false);
    setProjectAction(null);
    setProjectName("");
  }

  function openProjectMenu() {
    setProjectMenuOpen((open) => !open);
    setProjectAction(null);
    setProjectName("");
  }

  function openProjectAction(action: Exclude<ProjectAction, null>) {
    setProjectAction(action);
    setProjectName(action === "rename" ? activeProject?.name || "" : "");
  }

  function focusFirstProjectMenuItem() {
    requestAnimationFrame(() => {
      projectMenuRef.current
        ?.querySelector<HTMLButtonElement>("button:not(:disabled)")
        ?.focus();
    });
  }

  function handleProjectTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowDown") return;
    event.preventDefault();
    setProjectMenuOpen(true);
    setProjectAction(null);
    focusFirstProjectMenuItem();
  }

  function handleProjectMenuKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = Array.from(
      event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
    );
    if (!items.length) return;

    event.preventDefault();
    const currentIndex = items.indexOf(document.activeElement as HTMLButtonElement);
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : event.key === "ArrowUp"
            ? (currentIndex - 1 + items.length) % items.length
            : (currentIndex + 1) % items.length;
    items[nextIndex]?.focus();
  }

  async function submitProjectName() {
    const name = projectName.trim();
    if (!name) return;

    setProjectPending(true);
    try {
      if (projectAction === "create") {
        await onCreateProject(name);
      } else if (projectAction === "rename" && activeProject) {
        await onRenameProject(activeProject, name);
      }
      closeProjectMenu();
    } catch {
      // The workspace reports the API error and leaves the editor open to retry.
    } finally {
      setProjectPending(false);
    }
  }

  async function deleteProject() {
    if (!activeProject) return;

    setProjectPending(true);
    try {
      await onDeleteProject(activeProject);
      closeProjectMenu();
    } catch {
      // The workspace reports the API error and leaves confirmation open to retry.
    } finally {
      setProjectPending(false);
    }
  }

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
          <XIcon aria-hidden="true" />
        </button>
      </div>

      <div className="project-control" ref={projectControlRef}>
        <div className="project-switcher">
          <button
            className="project-select-trigger"
            ref={projectTriggerRef}
            type="button"
            aria-controls="project-popover"
            aria-expanded={projectMenuOpen}
            aria-haspopup="menu"
            onClick={openProjectMenu}
            onKeyDown={handleProjectTriggerKeyDown}
          >
            <BriefcaseIcon aria-hidden="true" weight="regular" />
            <span>{activeProject?.name || "Choose project"}</span>
            <CaretDownIcon
              aria-hidden="true"
              className={projectMenuOpen ? "project-chevron open" : "project-chevron"}
              weight="bold"
            />
          </button>
        </div>

        {projectMenuOpen ? (
          <div className="project-popover" id="project-popover">
            {projectAction === "create" || projectAction === "rename" ? (
              <div
                className="project-editor"
                role="dialog"
                aria-label={
                  projectAction === "create" ? "Create project" : "Rename project"
                }
              >
                <div className="project-editor-heading">
                  <strong>
                    {projectAction === "create" ? "Create project" : "Rename project"}
                  </strong>
                  <button
                    type="button"
                    aria-label="Close project editor"
                    onClick={() => setProjectAction(null)}
                  >
                    <XIcon aria-hidden="true" />
                  </button>
                </div>
                <form
                  onSubmit={(event) => {
                    event.preventDefault();
                    void submitProjectName();
                  }}
                >
                  <label htmlFor="project-name">Project name</label>
                  <input
                    id="project-name"
                    ref={projectInputRef}
                    value={projectName}
                    maxLength={120}
                    disabled={projectPending}
                    onChange={(event) => setProjectName(event.target.value)}
                  />
                  <div className="project-editor-actions">
                    <button
                      type="button"
                      disabled={projectPending}
                      onClick={() => setProjectAction(null)}
                    >
                      Cancel
                    </button>
                    <button
                      className="project-editor-submit"
                      type="submit"
                      disabled={!projectName.trim() || projectPending}
                    >
                      {projectPending
                        ? "Saving…"
                        : projectAction === "create"
                          ? "Create"
                          : "Save"}
                    </button>
                  </div>
                </form>
              </div>
            ) : projectAction === "delete" && activeProject ? (
              <div
                className="project-delete-confirm"
                role="alertdialog"
                aria-labelledby="delete-project-heading"
              >
                <TrashIcon aria-hidden="true" weight="regular" />
                <h3 id="delete-project-heading">Delete project?</h3>
                <p>
                  “{activeProject.name}” and all of its research and files will be
                  permanently deleted.
                </p>
                <div className="project-editor-actions">
                  <button
                    type="button"
                    disabled={projectPending}
                    onClick={() => setProjectAction(null)}
                  >
                    Cancel
                  </button>
                  <button
                    className="project-delete-submit"
                    type="button"
                    disabled={projectPending}
                    onClick={() => void deleteProject()}
                  >
                    {projectPending ? "Deleting…" : "Delete project"}
                  </button>
                </div>
              </div>
            ) : (
              <div
                className="project-menu"
                ref={projectMenuRef}
                role="menu"
                aria-label="Projects"
                onKeyDown={handleProjectMenuKeyDown}
              >
                <div className="project-list" role="group" aria-label="Choose project">
                  {projects.map((project) => {
                    const active = project.id === activeProject?.id;
                    return (
                      <button
                        key={project.id}
                        type="button"
                        role="menuitemradio"
                        aria-checked={active}
                        onClick={() => {
                          if (active) {
                            closeProjectMenu();
                          } else {
                            void onSelectProject(project)
                              .then(closeProjectMenu)
                              .catch(() => undefined);
                          }
                        }}
                      >
                        <FolderIcon aria-hidden="true" weight="regular" />
                        <span>{project.name}</span>
                        {active ? (
                          <CheckIcon
                            aria-hidden="true"
                            className="project-active-check"
                            weight="bold"
                          />
                        ) : null}
                      </button>
                    );
                  })}
                </div>
                <div className="project-menu-separator" />
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => openProjectAction("create")}
                >
                  <PlusIcon aria-hidden="true" />
                  <span>Create project</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  disabled={!activeProject}
                  onClick={() => openProjectAction("rename")}
                >
                  <PencilSimpleIcon aria-hidden="true" />
                  <span>Rename project</span>
                </button>
                <button
                  type="button"
                  className="project-menu-delete"
                  role="menuitem"
                  disabled={!activeProject}
                  onClick={() => openProjectAction("delete")}
                >
                  <TrashIcon aria-hidden="true" />
                  <span>Delete project</span>
                </button>
              </div>
            )}
          </div>
        ) : null}
      </div>

      <button className="new-thread" type="button" onClick={onCreate}>
        <PlusIcon aria-hidden="true" weight="bold" />
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
                    <FileTextIcon aria-hidden="true" weight="regular" />
                    <span className="thread-title">{thread.title}</span>
                  </button>
                  <button
                    className="thread-delete"
                    type="button"
                    title={`Delete ${thread.title}`}
                    aria-label={`Delete ${thread.title}`}
                    onClick={() => setConfirmingThreadId(thread.id)}
                  >
                    <TrashIcon aria-hidden="true" />
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
        <SignOutIcon aria-hidden="true" />
        Sign out
      </button>
    </aside>
  );
}
