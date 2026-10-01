export function cssRgb(value: string): [number, number, number] | null {
  const hex = value.trim().match(/^#([\da-f]{6}|[\da-f]{3})$/i)?.[1];
  if (hex) {
    const full = hex.length === 3 ? [...hex].map((digit) => digit + digit).join("") : hex;
    return [0, 2, 4].map((offset) => parseInt(full.slice(offset, offset + 2), 16)) as [number, number, number];
  }
  const rgb = value.trim().match(/^rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)(?:\s*[,/]\s*[\d.]+)?\s*\)$/i);
  if (!rgb) return null;
  const channels = rgb.slice(1, 4).map(Number);
  return channels.every((channel) => channel >= 0 && channel <= 255) ? channels as [number, number, number] : null;
}
