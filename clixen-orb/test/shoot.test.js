import test from "node:test";
import assert from "node:assert/strict";
import { standoff, phaseAt, T_FIRE, T_IMPACT, T_END, B_DRAW, STANDOFF } from "../src/shoot.js";

test("bunny stops STANDOFF px short of the target on the line from where it started", () => {
  const s = standoff([0, 0], [600, 0]);
  assert.deepEqual(s, [600 - STANDOFF, 0]);
  const t = standoff([100, 100], [100, 700]);
  assert.equal(Math.round(Math.hypot(t[0] - 100, t[1] - 700)), STANDOFF);
});

test("a short hop does not push the bunny past its start", () => {
  const s = standoff([0, 0], [100, 0]);
  assert.ok(s[0] > 0 && s[0] < 100); // stops at 80% of the way, not 170 px before
});

test("phases follow draw, aim, rocket, boom and impact is the sum of the first three", () => {
  assert.equal(phaseAt(0).name, "draw");
  assert.equal(phaseAt(B_DRAW + 0.01).name, "aim");
  assert.equal(phaseAt(T_FIRE + 0.01).name, "rocket");
  assert.equal(phaseAt(T_IMPACT + 0.01).name, "boom");
  assert.equal(phaseAt(T_END + 5).u, 1);
  assert.ok(Math.abs(T_IMPACT - (0.30 + 0.12 + 0.25)) < 1e-9);
});

test("standoff stays on screen: far side of the target, or clamped", () => {
  // target at the left edge, bunny coming from the left: the near spot is off-screen, so stand on the far side
  const s = standoff([300, 500], [35, 500], [1470, 956]);
  assert.ok(s[0] >= 40 && s[0] > 35);
  // nowhere fits: clamped inside the margin
  const c = standoff([100, 100], [20, 20], [200, 200]);
  assert.ok(c[0] >= 40 && c[1] >= 40 && c[0] <= 160 && c[1] <= 160);
  // plenty of room: unchanged
  assert.deepEqual(standoff([0, 500], [600, 500], [1470, 956]), [600 - STANDOFF, 500]);
});
