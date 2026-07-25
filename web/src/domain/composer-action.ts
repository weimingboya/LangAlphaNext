export type ComposerAction = "cancel" | "enqueue" | "interrupt";

export function composerAction(
  activeRunId: string | null,
  message: string,
): ComposerAction {
  if (activeRunId && !message.trim()) return "cancel";
  return activeRunId ? "interrupt" : "enqueue";
}
