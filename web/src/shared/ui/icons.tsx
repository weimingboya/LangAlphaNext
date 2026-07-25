import type { SVGProps } from "react";

export type IconProps = SVGProps<SVGSVGElement>;

export function CloseIcon(props: IconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}

export function MenuIcon(props: IconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

export function ContextIcon(props: IconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path d="M14 4v16M8 8v8" />
    </svg>
  );
}

export function PlusIcon(props: IconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

export function PaperclipIcon(props: IconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="M8 12.5 14.7 5.8a3 3 0 0 1 4.3 4.3l-8.5 8.5a5 5 0 0 1-7.1-7.1l8-8" />
    </svg>
  );
}

export function SendIcon(props: IconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="m5 12 14-7-4.5 14-3-5.5L5 12Z" />
      <path d="m11.5 13.5 4-4" />
    </svg>
  );
}

export function StopIcon(props: IconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
    </svg>
  );
}

export function TrashIcon(props: IconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <path d="M5 7h14M9 7V4h6v3M8 10v7M12 10v7M16 10v7M7 7l1 13h8l1-13" />
    </svg>
  );
}

export function SearchIcon(props: IconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m16 16 4 4" />
    </svg>
  );
}

export function FileIcon() {
  return (
    <span className="file-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24">
        <path d="M6 2.5h8l4 4V21.5H6z" />
        <path d="M14 2.5v4h4M9 12h6M9 16h6" />
      </svg>
    </span>
  );
}
