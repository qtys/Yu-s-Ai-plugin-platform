// Split by Unicode characters, never silently truncate a reply or cut a surrogate pair.
export function splitSpeechText(text: string, limit = 3000): string[] {
  if (!Number.isInteger(limit) || limit < 1) throw new Error("invalid speech chunk limit");
  const characters = Array.from(text.trim());
  const chunks: string[] = [];
  while (characters.length) {
    let end = Math.min(limit, characters.length);
    if (end < characters.length) {
      for (let index = end - 1; index >= Math.floor(end / 2); index--) {
        if (/[。！？.!?\n]/u.test(characters[index])) { end = index + 1; break; }
      }
    }
    chunks.push(characters.splice(0, end).join(""));
  }
  return chunks;
}
