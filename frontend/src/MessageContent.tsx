import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export type MessageDisplayMode = "markdown" | "plain" | "raw";

export function filterMarkdown(content: string): string {
  return content
    .replace(/```[^\n]*\n?([\s\S]*?)```/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}(#{1,6}|>|[-+*]|\d+[.)])\s+/gm, "")
    .replace(/^\s*[-+*]\s+\[[ xX]\]\s+/gm, "")
    .replace(/(\*\*|__|~~)(.*?)\1/g, "$2")
    .replace(/([*_])([^\n]+?)\1/g, "$2")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/<[^>]+>/g, "")
    .replace(/\\([\\`*_[\]{}()#+.!>|~-])/g, "$1")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function MessageContent({ content, mode }: { content: string; mode: MessageDisplayMode }) {
  if (mode === "plain") return <p>{filterMarkdown(content)}</p>;
  if (mode === "raw") return <p>{content}</p>;
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" /> }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MessageContent);
