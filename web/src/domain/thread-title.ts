export const DEFAULT_THREAD_TITLE = "New research";

const MAX_THREAD_TITLE_LENGTH = 48;
const MIN_WORD_BOUNDARY = 24;

export function isDefaultThreadTitle(title: string): boolean {
  return title.trim().toLowerCase() === DEFAULT_THREAD_TITLE.toLowerCase();
}

export function threadTitleFromMessage(message: string): string {
  const normalized = message.replace(/\s+/g, " ").trim();
  if (!normalized) return DEFAULT_THREAD_TITLE;

  const characters = Array.from(normalized);
  if (characters.length <= MAX_THREAD_TITLE_LENGTH) return normalized;

  const candidate = characters.slice(0, MAX_THREAD_TITLE_LENGTH - 1).join("");
  const lastSpace = candidate.lastIndexOf(" ");
  const title =
    lastSpace >= MIN_WORD_BOUNDARY ? candidate.slice(0, lastSpace) : candidate;
  return `${title.trimEnd()}…`;
}
