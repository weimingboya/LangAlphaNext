import Markdown, {
  type Components,
  type Options as MarkdownOptions,
} from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

import { fileReferenceSegments } from "../../domain/message-references";
import type { Asset, Citation } from "../../domain/types";

const REMARK_PLUGINS: NonNullable<MarkdownOptions["remarkPlugins"]> = [
  remarkGfm,
  [remarkMath, { singleDollarTextMath: false }],
];
const REHYPE_PLUGINS = [rehypeKatex];

function safeExternalUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

function markdownWithFriendlyFiles(text: string, assets: Asset[]): string {
  return fileReferenceSegments(text)
    .map((segment) => {
      if (segment.kind === "text") return segment.value;
      if (segment.kind === "citation") return "";
      const asset = assets.find((candidate) => candidate.sandbox_path === segment.path);
      const filename = asset?.filename || segment.path.split("/").at(-1) || "file";
      return `\`@${filename.replaceAll("`", "'")}\``;
    })
    .join("");
}

const MARKDOWN_COMPONENTS: Components = {
  a({ children, href, ...props }) {
    const external = href ? safeExternalUrl(href) : null;
    if (!external) return <span>{children}</span>;
    return (
      <a {...props} href={external} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
};

function CitationLinks({ citations }: { citations: Citation[] }) {
  const seen = new Set<string>();
  const safe = citations.flatMap((citation) => {
    const url = safeExternalUrl(citation.url);
    if (!url || seen.has(url)) return [];
    seen.add(url);
    return [{ ...citation, url }];
  });
  if (!safe.length) return null;
  return (
    <footer className="message-sources" aria-label="Message sources">
      <span>Sources</span>
      <ol>
        {safe.map((citation) => (
          <li key={citation.url}>
            <a href={citation.url} target="_blank" rel="noopener noreferrer">
              {citation.title || new URL(citation.url).hostname}
            </a>
          </li>
        ))}
      </ol>
    </footer>
  );
}

export function MarkdownMessage({
  assets,
  citations,
  text,
}: {
  assets: Asset[];
  citations: Citation[];
  text: string;
}) {
  return (
    <>
      <div className="markdown-content">
        <Markdown
          components={MARKDOWN_COMPONENTS}
          rehypePlugins={REHYPE_PLUGINS}
          remarkPlugins={REMARK_PLUGINS}
          skipHtml
        >
          {markdownWithFriendlyFiles(text, assets)}
        </Markdown>
      </div>
      <CitationLinks citations={citations} />
    </>
  );
}

export default MarkdownMessage;
