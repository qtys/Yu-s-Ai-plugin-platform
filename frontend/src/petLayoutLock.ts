type AnchorCanvas = Pick<HTMLElement, "style" | "setAttribute" | "removeAttribute">;

/** An imperative attribute is deliberately independent of React's className. */
export function setPetLayoutAnchor(canvas: AnchorCanvas, x: number, y: number) {
  canvas.style.setProperty("--layout-pet-x", `${x}px`);
  canvas.style.setProperty("--layout-pet-y", `${y}px`);
  canvas.setAttribute("data-layout-anchor", "locked");
}

export function clearPetLayoutAnchor(canvas: AnchorCanvas) {
  // Disable the overriding CSS before discarding its required coordinates.
  canvas.removeAttribute("data-layout-anchor");
  canvas.style.removeProperty("--layout-pet-x");
  canvas.style.removeProperty("--layout-pet-y");
}
