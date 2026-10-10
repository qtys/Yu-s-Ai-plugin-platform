export type BubbleBounds = { left: number; top: number; maxWidth: number; visible: boolean };
type Space = { width: number; height: number; petX: number; petY: number; inputX: number; inputY: number };

/** Newest sentences stay above when possible; only overflow uses the nearest side. */
export function placeSatelliteBubbles(sizes: { width: number; height: number; sideHeight?: number }[], space: Space): BubbleBounds[] {
  const margin = 12, gap = 7;
  const result: BubbleBounds[] = sizes.map(() => ({ left: 0, top: 0, maxWidth: space.width - 2 * margin, visible: false }));
  let aboveBottom = space.petY - 12;
  let usedAbove = false;
  const overflow: number[] = [];
  const leftEdge = Math.min(space.petX - 70 - 12, space.inputX - 12);
  const rightEdge = Math.max(space.petX + 70 + 12, space.inputX + 200 + 12);
  const leftRoom = leftEdge - margin;
  const rightRoom = space.width - margin - rightEdge;
  // Prefer left, unless it is too narrow to read. Keep the edge gap fixed,
  // rather than centering the side column in all available screen space.
  const useLeft = leftRoom >= Math.min(160, Math.max(...sizes.map(size => size.width), 0)) || leftRoom >= rightRoom;
  const sideWidth = Math.max(1, useLeft ? leftRoom : rightRoom);
  for (let index = sizes.length - 1; index >= 0; index--) {
    const size = sizes[index];
    const width = Math.min(size.width, space.width - 2 * margin);
    const top = aboveBottom - size.height;
    if (top >= margin) {
      result[index] = { left: Math.max(margin, Math.min(space.petX - width / 2, space.width - margin - width)), top, maxWidth: space.width - 2 * margin, visible: true };
      aboveBottom = top - gap;
      usedAbove = true;
    } else {
      overflow.push(index);
    }
  }
  let sideTop = usedAbove ? space.petY + 4 : margin;
  for (const index of overflow) {
    const size = sizes[index];
    const sideBubbleWidth = Math.min(size.width, sideWidth);
    const sideHeight = size.sideHeight ?? size.height;
    // Keep side overflow out of the above column's vertical range.
    const nextTop = usedAbove ? Math.max(sideTop, space.petY + 4) : sideTop;
    result[index] = { left: useLeft ? leftEdge - sideBubbleWidth : rightEdge, top: nextTop, maxWidth: sideWidth, visible: nextTop + sideHeight <= space.height - margin };
    sideTop = nextTop + sideHeight + gap;
  }
  return result;
}
