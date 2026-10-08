/** Resume at the end of received sentences, retaining only the latest four. */
export function resumePetBubbles(sentences: string[]) {
  return { cursor: sentences.length, visible: sentences.slice(-4) };
}
