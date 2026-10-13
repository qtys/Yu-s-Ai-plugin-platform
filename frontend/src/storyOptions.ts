export type StoryOption = { number: string; label: string; text: string };

// Only the explicitly marked ordinary branches become controls. Everything
// else remains text; this is a display parser, never an HTML/script interpreter.
export function parseStoryOptions(content: string, allowed?: Set<string>): { body: string; options: StoryOption[] } {
  const lines = content.split("\n");
  let fence: { marker: string; length: number } | null = null;
  let heading = -1;
  lines.forEach((line, index) => {
    const delimiter = /^\s{0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (delimiter) {
      if (!fence) fence = { marker: delimiter[1][0], length: delimiter[1].length };
      else if (delimiter[1][0] === fence.marker && delimiter[1].length >= fence.length && !delimiter[2].trim()) fence = null;
      return;
    }
    if (!fence && /^\s*『剧情发展』\s*$/.test(line)) heading = index;
  });
  if (heading < 0) return { body: content, options: [] };
  const options: StoryOption[] = [];
  const removed = new Set<number>();
  let closing = -1;
  let unsupported = false;
  for (let i = heading + 1; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line) continue;
    if (/^<\/Status_block>$/i.test(line)) { closing = i; break; }
    const match = /^(\d{1,2})[.、)]\s*\[([^\]\r\n]{1,30})\]\s*(.+)$/.exec(line);
    if (!match) {
      // Unsupported numbered branches remain readable, but never clickable.
      if (/^\d{1,2}[.、)]/.test(line)) { unsupported = true; continue; }
      break;
    }
    const text = match[3].trim();
    if (allowed && !allowed.has(`${match[1]}\u0000${match[2].trim()}\u0000${text}`)) { unsupported = true; continue; }
    if (text.length > 500) { unsupported = true; continue; }
    if (options.length >= 8 || options.some((option) => option.number === match[1])) { unsupported = true; continue; }
    options.push({ number: match[1], label: match[2].trim(), text });
    removed.add(i);
  }
  if (!options.length) return { body: content, options };
  if (!unsupported) {
    removed.add(heading);
    if (closing >= 0) removed.add(closing);
  }
  return { body: lines.filter((_, index) => !removed.has(index)).join("\n"), options };
}
