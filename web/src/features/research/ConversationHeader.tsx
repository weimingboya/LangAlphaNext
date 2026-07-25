import { ContextIcon, MenuIcon } from "../../shared/ui/icons";

interface ConversationHeaderProps {
  assetCount: number;
  contextOpen: boolean;
  onOpenThreads: () => void;
  onToggleContext: () => void;
  title: string;
}

export function ConversationHeader({
  assetCount,
  contextOpen,
  onOpenThreads,
  onToggleContext,
  title,
}: ConversationHeaderProps) {
  return (
    <header className="conversation-header">
      <button
        className="icon-button mobile-only"
        type="button"
        onClick={onOpenThreads}
        aria-label="Open conversations"
      >
        <MenuIcon />
      </button>
      <h2>{title}</h2>
      <button
        className="context-toggle"
        type="button"
        onClick={onToggleContext}
        aria-label="Toggle context"
        aria-expanded={contextOpen}
        aria-controls="context-panel"
      >
        <ContextIcon />
        <span className="desktop-only">Context</span>
        {assetCount ? <span className="context-count">{assetCount}</span> : null}
      </button>
    </header>
  );
}
