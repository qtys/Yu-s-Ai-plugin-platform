/** Keep newest bubbles intact; retire older ones if the screen cannot fit all four. */
export function fitPetBubbleCount(heights: number[], availableHeight: number): number {
  let used = 0;
  let count = 0;
  for (const height of heights.slice(-4).reverse()) {
    const next = used + height + (count ? 7 : 0);
    if (count && next > availableHeight) break;
    used = next;
    count++;
  }
  return count;
}
