import Papa from "papaparse";
import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import type { Asset } from "../../domain/types";
import {
  ArrowLeftIcon,
  CloseIcon,
  DownloadIcon,
  FileIcon,
} from "../../shared/ui/icons";

const MarkdownMessage = lazy(() => import("../research/MarkdownMessage"));

type PreviewKind =
  | "audio"
  | "csv"
  | "html"
  | "image"
  | "json"
  | "markdown"
  | "pdf"
  | "text"
  | "unsupported"
  | "video";

const TEXT_EXTENSIONS = new Set([
  "css",
  "env",
  "ini",
  "js",
  "jsx",
  "log",
  "py",
  "sql",
  "toml",
  "ts",
  "tsx",
  "txt",
  "xml",
  "yaml",
  "yml",
]);

function extension(filename: string): string {
  return filename.toLocaleLowerCase().split(".").at(-1) || "";
}

export function previewKind(asset: Asset): PreviewKind {
  const mediaType = asset.media_type.split(";")[0].trim().toLocaleLowerCase();
  const ext = extension(asset.filename);
  if (["md", "markdown", "mdown", "mkd"].includes(ext) || mediaType === "text/markdown") {
    return "markdown";
  }
  if (["csv", "tsv"].includes(ext) || ["text/csv", "text/tab-separated-values"].includes(mediaType)) {
    return "csv";
  }
  if (ext === "pdf" || mediaType === "application/pdf") return "pdf";
  if (["html", "htm"].includes(ext) || ["text/html", "application/xhtml+xml"].includes(mediaType)) {
    return "html";
  }
  if (ext === "svg" || mediaType === "image/svg+xml") return "unsupported";
  if (mediaType.startsWith("image/")) return "image";
  if (mediaType.startsWith("video/")) return "video";
  if (mediaType.startsWith("audio/")) return "audio";
  if (ext === "json" || mediaType === "application/json") return "json";
  if (mediaType.startsWith("text/") || TEXT_EXTENSIONS.has(ext)) return "text";
  return "unsupported";
}

function previewLabel(kind: PreviewKind): string {
  const labels: Record<PreviewKind, string> = {
    audio: "Audio",
    csv: "Table",
    html: "HTML",
    image: "Image",
    json: "JSON",
    markdown: "Markdown",
    pdf: "PDF",
    text: "Text",
    unsupported: "File",
    video: "Video",
  };
  return labels[kind];
}

function formatBytes(value?: number | null): string | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

function CsvPreview({ content }: { content: string }) {
  const result = useMemo(
    () =>
      Papa.parse<string[]>(content, {
        preview: 501,
        skipEmptyLines: "greedy",
      }),
    [content],
  );
  const rows = result.data;
  const width = Math.min(100, Math.max(0, ...rows.map((row) => row.length)));
  const headers = Array.from({ length: width }, (_, index) =>
    rows[0]?.[index]?.trim() || `Column ${index + 1}`,
  );
  const body = rows.slice(1, 501);

  if (!rows.length || !width) {
    return <div className="preview-empty">This table is empty.</div>;
  }

  return (
    <div className="csv-preview">
      <div className="csv-summary">
        <span>{body.length.toLocaleString()} preview rows</span>
        <span>{headers.length.toLocaleString()} columns</span>
        {result.meta.delimiter ? <span>Delimiter: {JSON.stringify(result.meta.delimiter)}</span> : null}
      </div>
      {result.errors.length ? (
        <p className="preview-notice">
          Some rows could not be read cleanly: {result.errors[0].message}
        </p>
      ) : null}
      <div className="csv-table-wrap">
        <table>
          <thead>
            <tr>
              <th aria-label="Row number">#</th>
              {headers.map((header, index) => (
                <th key={`${header}:${index}`}>{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={rowIndex}>
                <th>{rowIndex + 1}</th>
                {headers.map((_, columnIndex) => (
                  <td key={columnIndex}>{row[columnIndex] || ""}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length >= 501 ? (
        <p className="preview-limit">Showing the first 500 rows for a fast preview.</p>
      ) : null}
    </div>
  );
}

function TextPreview({ content, kind }: { content: string; kind: PreviewKind }) {
  if (kind === "markdown") {
    return (
      <article className="document-preview markdown-preview">
        <Suspense fallback={<div className="preview-state">Rendering document…</div>}>
          <MarkdownMessage assets={[]} citations={[]} text={content} />
        </Suspense>
      </article>
    );
  }
  if (kind === "csv") return <CsvPreview content={content} />;
  let display = content;
  if (kind === "json") {
    try {
      display = JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      // Preserve malformed JSON so the user can still inspect the source.
    }
  }
  return <pre className="plain-text-preview">{display}</pre>;
}

interface FilePreviewPanelProps {
  asset: Asset;
  onBack: () => void;
  onClose: () => void;
  onDownload: (asset: Asset) => Promise<void>;
}

export function FilePreviewPanel({
  asset,
  onBack,
  onClose,
  onDownload,
}: FilePreviewPanelProps) {
  const kind = previewKind(asset);
  const viewUrl = `/api/assets/${asset.id}/view`;
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const textual = ["csv", "json", "markdown", "text"].includes(kind);

  useEffect(() => {
    if (!textual) {
      setContent("");
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setContent("");
    setError(null);
    setLoading(true);
    fetch(viewUrl, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Preview unavailable (${response.status})`);
        return response.text();
      })
      .then(setContent)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [asset.id, textual, viewUrl]);

  const size = formatBytes(asset.size_bytes);
  return (
    <aside className="context-rail file-preview-rail" aria-label={`Preview ${asset.filename}`}>
      <header className="preview-header">
        <button className="icon-button" type="button" onClick={onBack} aria-label="Back to context">
          <ArrowLeftIcon />
        </button>
        <div className="preview-title">
          <strong title={asset.filename}>{asset.filename}</strong>
          <small>{[previewLabel(kind), size].filter(Boolean).join(" · ")}</small>
        </div>
        <button
          className="icon-button"
          type="button"
          onClick={() => void onDownload(asset)}
          aria-label={`Download ${asset.filename}`}
        >
          <DownloadIcon />
        </button>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close preview">
          <CloseIcon />
        </button>
      </header>

      <div className={`preview-body ${kind}-body`}>
        {loading ? <div className="preview-state">Loading preview…</div> : null}
        {error ? (
          <div className="preview-state preview-error">
            <FileIcon />
            <h3>Preview unavailable</h3>
            <p>{error}</p>
            <button type="button" onClick={() => void onDownload(asset)}>Download file</button>
          </div>
        ) : null}
        {!loading && !error && textual ? <TextPreview content={content} kind={kind} /> : null}
        {!loading && !error && kind === "pdf" ? (
          <iframe className="native-document-frame" src={viewUrl} title={`Preview ${asset.filename}`} />
        ) : null}
        {!loading && !error && kind === "html" ? (
          <iframe
            className="native-document-frame"
            src={viewUrl}
            title={`Preview ${asset.filename}`}
            referrerPolicy="no-referrer"
            sandbox="allow-scripts"
          />
        ) : null}
        {!loading && !error && kind === "image" ? (
          <div className="media-preview"><img src={viewUrl} alt={asset.filename} /></div>
        ) : null}
        {!loading && !error && kind === "video" ? (
          <div className="media-preview"><video src={viewUrl} controls /></div>
        ) : null}
        {!loading && !error && kind === "audio" ? (
          <div className="media-preview"><audio src={viewUrl} controls /></div>
        ) : null}
        {!loading && !error && kind === "unsupported" ? (
          <div className="preview-state">
            <FileIcon />
            <h3>No inline preview for this format</h3>
            <p>You can still download {asset.filename} and open it in its native app.</p>
            <button type="button" onClick={() => void onDownload(asset)}>Download file</button>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

export default FilePreviewPanel;
