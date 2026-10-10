/** A first utterance is new, even though every string starts with an empty prefix. */
export function isFreshPetReply(text: string, previous: string) {
  return !text || !previous || !text.startsWith(previous);
}

/** Resume at the end of received sentences, retaining only the latest four. */
export function resumePetBubbles(sentences: string[]) {
  return { cursor: sentences.length, visible: sentences.slice(-4) };
}
