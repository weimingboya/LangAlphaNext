import { useEffect, useMemo, useRef } from "react";

import { CloseIcon, FileIcon, SearchIcon } from "../../shared/ui/icons";
import type { Asset } from "../../domain/types";

interface FilePickerProps {
  assets: Asset[];
  focusSearch: boolean;
  onClose: () => void;
  onQueryChange: (query: string) => void;
  onSelect: (asset: Asset) => void;
  open: boolean;
  query: string;
}

function artifactKind(asset: Asset): string {
  return asset.role === "artifact" ? "Generated" : "Uploaded";
}

export function FilePicker({
  assets,
  focusSearch,
  onClose,
  onQueryChange,
  onSelect,
  open,
  query,
}: FilePickerProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return [...assets]
      .reverse()
      .filter((asset) => asset.filename.toLocaleLowerCase().includes(normalized));
  }, [assets, query]);

  useEffect(() => {
    if (open && focusSearch) {
      window.requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [focusSearch, open]);

  if (!open) return null;
  return (
    <div className="file-picker">
      <div className="file-search">
        <SearchIcon />
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              onClose();
            } else if (event.key === "Enter" && filtered[0]) {
              event.preventDefault();
              onSelect(filtered[0]);
            }
          }}
          placeholder="Search workspace files…"
          autoComplete="off"
        />
        <button
          className="icon-button"
          type="button"
          onClick={onClose}
          aria-label="Close file search"
        >
          <CloseIcon />
        </button>
      </div>
      <div className="file-results" role="listbox" aria-label="Workspace files">
        {filtered.length ? (
          filtered.map((asset, index) => (
            <button
              className={`file-option${index === 0 ? " active" : ""}`}
              key={asset.id}
              type="button"
              role="option"
              aria-selected={index === 0}
              onClick={() => onSelect(asset)}
            >
              <FileIcon />
              <span className="file-copy">
                <strong>{asset.filename}</strong>
                <small>{asset.media_type || "Workspace file"}</small>
              </span>
              <span className="file-kind">{artifactKind(asset)}</span>
            </button>
          ))
        ) : (
          <p className="context-empty">
            {assets.length
              ? "No files match this search."
              : "Upload a file to reference it here."}
          </p>
        )}
      </div>
    </div>
  );
}
