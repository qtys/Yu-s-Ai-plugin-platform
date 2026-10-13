import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { parseStoryOptions } from "./storyOptions";
import type { StoryOption } from "./storyOptions";

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

function MessageContent({ content, mode, onSelectOption, approvedOptions = [], optionsDisabled = false }: { content: string; mode: MessageDisplayMode; onSelectOption?: (text: string) => void; approvedOptions?: StoryOption[]; optionsDisabled?: boolean }) {
  const allowed = new Set(approvedOptions.map((option) => `${option.number}\u0000${option.label}\u0000${option.text}`));
  const { body, options } = onSelectOption ? parseStoryOptions(content, allowed) : { body: content, options: [] };
  const rendered = mode === "plain" ? <p>{filterMarkdown(body)}</p> : mode === "raw" ? <p>{body}</p> : (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" /> }}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
  return <>{rendered}{options.length > 0 && <div className="story-options" role="group" aria-label="剧情选项">
    {options.map((option) => <button key={option.number} type="button" disabled={optionsDisabled} onClick={() => onSelectOption?.(option.text)} title="填入输入框，确认后发送"><span>{option.number}</span><span className="story-option-label">{option.label}</span><span>{option.text}</span></button>)}
    <small>点击填入输入框，由你确认发送</small>
  </div>}</>;
}

export default memo(MessageContent);
