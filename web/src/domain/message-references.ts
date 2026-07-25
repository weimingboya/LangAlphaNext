import type { Asset, Citation, RenderSegment } from "./types";

const FILE_REFERENCE_PATTERN = /@file\("([^"\n]+)"\)/g;

export function serializeFileReference(
  message: string,
  reference: Asset | null,
): string {
  const value = message.trim();
  if (!reference?.filename) return value;
  const path =
    reference.sandbox_path ||
    `/workspace/input/assets/${reference.id}/${reference.filename}`;
  const token = `@${reference.filename}`;
  if (!value.includes(token)) return value;
  return value.replace(token, `@file("${path}")`);
}

export function fileReferenceSegments(message: string): RenderSegment[] {
  const segments: RenderSegment[] = [];
  let cursor = 0;
  for (const match of message.matchAll(FILE_REFERENCE_PATTERN)) {
    const index = match.index;
    if (index > cursor) {
      segments.push({ kind: "text", value: message.slice(cursor, index) });
    }
    segments.push({ kind: "file", path: match[1] });
    cursor = index + match[0].length;
  }
  if (cursor < message.length) {
    segments.push({ kind: "text", value: message.slice(cursor) });
  }
  return segments;
}

type CitationRenderSegment = Extract<RenderSegment, { kind: "citation" }> & {
  end?: number;
};

function safeCitation(value: Citation, index: number): CitationRenderSegment | null {
  let parsed: URL;
  try {
    parsed = new URL(value.url);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(parsed.protocol)) return null;
  const end =
    value.end_index === null || value.end_index === undefined
      ? null
      : Number(value.end_index);
  return {
    kind: "citation",
    index,
    url: parsed.href,
    title: value.title || parsed.href,
    ...(end !== null && Number.isFinite(end) ? { end } : {}),
  };
}

export function citationSegments(
  message: string,
  citations: Citation[] = [],
): RenderSegment[] {
  const normalized: CitationRenderSegment[] = [];
  const seen = new Set<string>();
  for (const citation of citations) {
    const safe = safeCitation(citation, normalized.length + 1);
    if (!safe) continue;
    const key = `${safe.url}\n${citation.start_index ?? ""}\n${safe.end ?? ""}`;
    if (seen.has(key)) continue;
    seen.add(key);
    normalized.push(safe);
  }
  const positioned = normalized
    .filter((citation) => citation.end !== undefined)
    .map((citation) => ({
      ...citation,
      end: Math.max(0, Math.min(message.length, citation.end ?? message.length)),
    }))
    .sort((left, right) => left.end - right.end || left.index - right.index);
  const trailing = normalized.filter((citation) => citation.end === undefined);
  const segments: RenderSegment[] = [];
  let cursor = 0;
  for (const citation of positioned) {
    if (citation.end > cursor) {
      segments.push({ kind: "text", value: message.slice(cursor, citation.end) });
      cursor = citation.end;
    }
    const { end: _end, ...renderable } = citation;
    segments.push(renderable);
  }
  if (cursor < message.length) {
    segments.push({ kind: "text", value: message.slice(cursor) });
  }
  for (const citation of trailing) {
    const { end: _end, ...renderable } = citation;
    segments.push(renderable);
  }
  return segments;
}
