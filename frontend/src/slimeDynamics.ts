export type Spring = { value: number; velocity: number };

/** Small bounded integration steps keep the same feel across refresh rates. */
export function advanceSpring(state: Spring, target: number, elapsed: number, stiffness = 150, damping = 22): Spring {
  let { value, velocity } = state;
  let remaining = Math.max(0, Math.min(.064, elapsed));
  while (remaining > 0) {
    const dt = Math.min(1 / 240, remaining);
    velocity += ((target - value) * stiffness - velocity * damping) * dt;
    value += velocity * dt;
    remaining -= dt;
  }
  return { value, velocity };
}

export function dentMap(x: number, y: number) {
  const cx = Math.max(15, Math.min(155, x));
  const cy = Math.max(20, Math.min(170, y));
  // Neutral RG=128 outside the touch; directional displacement only near it.
  const red = Math.round(128 + (cx - 85) / 85 * 110);
  const green = Math.round(128 + (cy - 95) / 95 * 110);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="170" height="190"><defs><radialGradient id="d" gradientUnits="userSpaceOnUse" cx="${cx}" cy="${cy}" r="48"><stop stop-color="rgb(${red},${green},128)"/><stop offset="1" stop-color="rgb(128,128,128)"/></radialGradient></defs><rect width="170" height="190" fill="url(#d)"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}
