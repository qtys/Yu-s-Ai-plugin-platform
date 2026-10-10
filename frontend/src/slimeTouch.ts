export type TouchKind = "cheek" | "head" | "boop" | "protest";
export type TouchMemory = { kind: TouchKind; side: "left" | "right"; until: number };
export type TouchHistory = { recent: number[]; lastProtest: number };

export function rememberTouch(history: TouchHistory, x: number, y: number, now: number, previous?: TouchMemory | null) {
  const recent = [...history.recent.filter(time => now - time < 1800), now].slice(-4);
  const protest = recent.length >= 3 && now - history.lastProtest >= 4000;
  const kind: TouchKind = protest ? "protest" : y < 60 ? "head" : y > 80 && (x < 53 || x > 87) ? "cheek" : "boop";
  return {
    history: { recent: protest ? [] : recent, lastProtest: protest ? now : history.lastProtest },
    memory: previous?.kind === "protest" && previous.until > now && !protest
      ? previous : { kind, side: x < 70 ? "left" : "right", until: now + (protest ? 6500 : 9000) } as TouchMemory,
  };
}

export function touchVisible(memory: TouchMemory | null, now: number, protectedExpression: boolean, enabled: boolean) {
  return !!memory && memory.until > now && enabled && !protectedExpression;
}
