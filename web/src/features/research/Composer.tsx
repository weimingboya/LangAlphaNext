import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { PaperclipIcon, SendIcon, StopIcon } from "../../shared/ui/icons";
import { composerAction } from "../../domain/composer-action";
import type { Asset } from "../../domain/types";
import { FilePicker } from "../assets/FilePicker";

interface ComposerProps {
  activeRunId: string | null;
  assets: Asset[];
  filePickerOpen: boolean;
  fileQuery: string;
  notify: (message: string) => void;
  onCancel: () => Promise<void>;
  onFilePickerOpen: (query?: string) => void;
  onFilePickerToggle: (open: boolean) => void;
  onFileQueryChange: (query: string) => void;
  onReferenceChange: (asset: Asset | null) => void;
  onSubmit: (message: string, strategy?: "enqueue" | "interrupt") => Promise<void>;
  onUpload: (file: File) => Promise<Asset>;
  selectedReference: Asset | null;
}

export function Composer({
  activeRunId,
  assets,
  filePickerOpen,
  fileQuery,
  notify,
  onCancel,
  onFilePickerOpen,
  onFilePickerToggle,
  onFileQueryChange,
  onReferenceChange,
  onSubmit,
  onUpload,
  selectedReference,
}: ComposerProps) {
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [pickerShouldFocus, setPickerShouldFocus] = useState(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const hasActiveRun = Boolean(activeRunId);
  const hasMessage = Boolean(message.trim());
  const primaryAction = composerAction(activeRunId, message);
  const isStopMode = primaryAction === "cancel";

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 150)}px`;
  }, [message]);

  function mentionQueryAtCursor(value: string): string | null {
    const cursor = textareaRef.current?.selectionStart ?? value.length;
    const match = value.slice(0, cursor).match(/(?:^|\s)@([^\s@]*)$/);
    return match ? match[1] : null;
  }

  function changeMessage(event: ChangeEvent<HTMLTextAreaElement>) {
    const value = event.target.value;
    setMessage(value);
    if (selectedReference && !value.includes(`@${selectedReference.filename}`)) {
      onReferenceChange(null);
    }
    const query = mentionQueryAtCursor(value);
    if (query !== null) {
      setPickerShouldFocus(false);
      onFilePickerOpen(query);
    }
  }

  function selectReference(asset: Asset) {
    const textarea = textareaRef.current;
    const cursor = textarea?.selectionStart ?? message.length;
    let next = message;
    let adjustedCursor = cursor;
    if (
      selectedReference &&
      selectedReference.id !== asset.id &&
      next.includes(`@${selectedReference.filename}`)
    ) {
      const previousToken = `@${selectedReference.filename}`;
      const previousIndex = next.indexOf(previousToken);
      next = next.replace(previousToken, "");
      if (previousIndex < adjustedCursor) {
        adjustedCursor = Math.max(0, adjustedCursor - previousToken.length);
      }
    }
    const before = next.slice(0, adjustedCursor);
    const trigger = before.match(/@([^\s@]*)$/);
    const start = trigger ? adjustedCursor - trigger[0].length : adjustedCursor;
    const prefix = start > 0 && !/\s/.test(next[start - 1]) ? " " : "";
    const suffix =
      adjustedCursor < next.length && !/\s/.test(next[adjustedCursor]) ? " " : "";
    next = `${next.slice(0, start)}${prefix}@${asset.filename}${suffix || " "}${next.slice(
      adjustedCursor,
    )}`;
    setMessage(next);
    onReferenceChange(asset);
    onFilePickerToggle(false);
    onFileQueryChange("");
    window.requestAnimationFrame(() => textarea?.focus());
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!hasMessage || submitting) return;
    if (primaryAction === "cancel") return;
    setSubmitting(true);
    try {
      await onSubmit(message, primaryAction);
      setMessage("");
      onReferenceChange(null);
      onFilePickerToggle(false);
      onFileQueryChange("");
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelActiveRun() {
    if (submitting) return;
    setSubmitting(true);
    try {
      await onCancel();
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const asset = await onUpload(file);
      selectReference(asset);
    } catch (reason) {
      notify(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape" && filePickerOpen) {
      event.preventDefault();
      onFilePickerToggle(false);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <div className="composer-dock">
      {hasActiveRun ? (
        <div className="run-strip" aria-live="polite">
          <span className="run-pulse" aria-hidden="true" />
          <span>
            {hasMessage
              ? "Researching… Send to stop and redirect."
              : "Researching…"}
          </span>
        </div>
      ) : null}

      <FilePicker
        assets={assets}
        focusSearch={pickerShouldFocus}
        onClose={() => {
          onFilePickerToggle(false);
          window.requestAnimationFrame(() => textareaRef.current?.focus());
        }}
        onQueryChange={onFileQueryChange}
        onSelect={selectReference}
        open={filePickerOpen}
        query={fileQuery}
      />

      <form className="composer" onSubmit={submit}>
        <label className="composer-action" title="Upload file">
          <PaperclipIcon />
          <span className="sr-only">Upload file</span>
          <input
            ref={fileInputRef}
            type="file"
            disabled={uploading}
            onChange={upload}
          />
        </label>
        <div className="composer-field">
          <textarea
            ref={textareaRef}
            rows={1}
            value={message}
            onChange={changeMessage}
            onKeyDown={keyDown}
            placeholder="Ask a follow-up or @ a file…"
            aria-label="Research message"
            required
          />
          <button
            className="mention-hint"
            type="button"
            onClick={() => {
              setPickerShouldFocus(true);
              onFilePickerOpen();
            }}
          >
            @ files
          </button>
        </div>
        <button
          className={`send-button${isStopMode ? " stop-mode" : ""}`}
          type={isStopMode ? "button" : "submit"}
          disabled={submitting || (!isStopMode && uploading)}
          aria-label={
            isStopMode
              ? "Stop research"
              : hasActiveRun
                ? "Stop and redirect"
                : "Send message"
          }
          title={
            isStopMode
              ? "Stop research"
              : hasActiveRun
                ? "Stop current research and send this direction"
                : "Send message"
          }
          onClick={isStopMode ? () => void cancelActiveRun() : undefined}
        >
          {isStopMode ? <StopIcon /> : <SendIcon />}
        </button>
      </form>
      <p className="composer-note">
        LangAlpha can make mistakes. Verify important financial decisions.
      </p>
    </div>
  );
}
