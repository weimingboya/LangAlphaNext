import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import type { Asset } from "../../domain/types";
import { FilePreviewPanel, previewKind } from "./FilePreviewPanel";

function asset(filename: string, mediaType = "application/octet-stream"): Asset {
  return {
    id: `asset-${filename}`,
    owner_id: "owner-1",
    project_id: "project-1",
    role: "input",
    status: "ready",
    logical_key: `input:${filename}`,
    object_path: `owner/project/${filename}`,
    filename,
    media_type: mediaType,
    size_bytes: 2048,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

describe("FilePreviewPanel", () => {
  test("recognizes previewable formats by media type and filename", () => {
    expect(previewKind(asset("report.md"))).toBe("markdown");
    expect(previewKind(asset("holdings.csv", "text/plain"))).toBe("csv");
    expect(previewKind(asset("filing.pdf"))).toBe("pdf");
    expect(previewKind(asset("photo.png", "image/png"))).toBe("image");
    expect(previewKind(asset("diagram.svg", "image/svg+xml"))).toBe("unsupported");
    expect(previewKind(asset("bundle.zip", "application/zip"))).toBe("unsupported");
  });

  test("renders PDF in the side pane with download and close controls", () => {
    const html = renderToStaticMarkup(
      <FilePreviewPanel
        asset={asset("filing.pdf", "application/pdf")}
        onBack={() => undefined}
        onClose={() => undefined}
        onDownload={async () => undefined}
      />,
    );

    expect(html).toContain('aria-label="Preview filing.pdf"');
    expect(html).toContain('src="/api/assets/asset-filing.pdf/view"');
    expect(html).toContain('aria-label="Download filing.pdf"');
    expect(html).toContain("PDF · 2.0 KB");
  });
});
