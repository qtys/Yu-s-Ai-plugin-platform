/** Completed sentences from one streamed response; an unfinished tail waits for EOF. */
export function splitPetSentences(text: string, finished = false): string[] {
  const sentences: string[] = [];
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    const period = char === "." && (i + 1 === text.length || /\s/.test(text[i + 1]));
    if (!/[。！？!?\n]/.test(char) && !period) continue;
    // Wait for punctuation/closing quotes to finish arriving in the next token.
    let end = i + 1;
    while (end < text.length && /[。！？!?.”’」』）)]/.test(text[end])) end++;
    if (end === text.length && !finished) break;
    const sentence = text.slice(start, end).trim();
    if (sentence) sentences.push(sentence);
    start = end;
    i = end - 1;
  }
  if (finished && text.slice(start).trim()) sentences.push(text.slice(start).trim());
  return sentences;
}
