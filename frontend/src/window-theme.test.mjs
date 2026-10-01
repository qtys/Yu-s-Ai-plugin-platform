import test from "node:test";
import assert from "node:assert/strict";
import { cssRgb } from "./window-theme.ts";

test("native colors match CSS hex and computed RGB", () => {
  assert.deepEqual(cssRgb(" #101114 "), [16, 17, 20]);
  assert.deepEqual(cssRgb("#fff"), [255, 255, 255]);
  assert.deepEqual(cssRgb("rgb(16, 17, 20)"), [16, 17, 20]);
  assert.deepEqual(cssRgb("rgba(255, 255, 255, 1)"), [255, 255, 255]);
});
test("reject invalid native color values", () => {
  for (const value of ["transparent", "url(x)", "rgb(256, 0, 0)", "#ffff", ""]) assert.equal(cssRgb(value), null);
});
