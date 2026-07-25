import { CloseIcon, FileIcon } from "../../shared/ui/icons";
import type { Asset, Citation } from "../../domain/types";

interface ContextPanelProps {
  assets: Asset[];
  citations: Citation[];
  onClose: () => void;
  onDownload: (asset: Asset) => void;
  onOpenHtml: (asset: Asset) => void;
  onReference: () => void;
}

function artifactKind(asset: Asset): string {
  return asset.role === "artifact" ? "Generated" : "Uploaded";
}

function formatArtifactDate(asset: Asset): string {
  if (!asset.created_at) return artifactKind(asset);
  const date = new Date(asset.created_at);
  if (Number.isNaN(date.valueOf())) return artifactKind(asset);
  return `${artifactKind(asset)} · ${date.toLocaleDateString([], {
    month: "short",
    day: "numeric",
  })}`;
}

function isHtml(asset: Asset): boolean {
  return asset.media_type.split(";")[0].trim().toLowerCase() === "text/html";
}

export function ContextPanel({
  assets,
  citations,
  onClose,
  onDownload,
  onOpenHtml,
  onReference,
}: ContextPanelProps) {
  return (
    <aside className="context-rail" id="context-panel" aria-label="Research context">
      <header className="context-header">
        <h2>Context</h2>
        <button
          className="icon-button"
          type="button"
          onClick={onClose}
          aria-label="Close context"
        >
          <CloseIcon />
        </button>
      </header>
      <section className="context-section">
        <h3>Sources</h3>
        <div className="context-list">
          {citations.length ? (
            citations.map((citation) => (
              <a
                className="context-file"
                href={citation.url}
                key={`${citation.url}:${citation.end_index}`}
                target="_blank"
                rel="noopener noreferrer"
              >
                <span className="file-copy">
                  <strong>{citation.title}</strong>
                  <small>{new URL(citation.url).hostname}</small>
                </span>
                <span className="file-chevron" aria-hidden="true" />
              </a>
            ))
          ) : (
            <p className="context-empty">Sources linked to an answer will appear here.</p>
          )}
        </div>
      </section>
      <section className="context-section">
        <div className="section-heading">
          <h3>Files</h3>
          <button className="section-action" type="button" onClick={onReference}>
            @ Reference
          </button>
        </div>
        <div className="context-list">
          {assets.length ? (
            [...assets].reverse().map((asset) => (
              <a
                className="context-file"
                href={isHtml(asset) ? `/api/assets/${asset.id}/view` : "#"}
                key={asset.id}
                onClick={(event) => {
                  event.preventDefault();
                  if (isHtml(asset)) onOpenHtml(asset);
                  else onDownload(asset);
                }}
              >
                <FileIcon />
                <span className="file-copy">
                  <strong>{asset.filename}</strong>
                  <small>{formatArtifactDate(asset)}</small>
                </span>
                <span className="file-chevron" aria-hidden="true" />
              </a>
            ))
          ) : (
            <p className="context-empty">No workspace files yet.</p>
          )}
        </div>
      </section>
    </aside>
  );
}
