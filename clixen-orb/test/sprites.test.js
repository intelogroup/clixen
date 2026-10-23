import test from "node:test";
import assert from "node:assert/strict";
import { frameAt, finished, leanOffsets } from "../src/sprites.js";

const loop = { frames: 8, fps: 10, loop: true };
const once = { frames: 8, fps: 10, loop: false };

test("loop wraps and starts at frame 0", () => {
  assert.equal(frameAt(loop, 0), 0);
  assert.equal(frameAt(loop, 0.35), 3);
  assert.equal(frameAt(loop, 0.8), 0);
  assert.equal(frameAt(loop, 1.05), 2);
});

test("one-shot holds its last frame and reports finished", () => {
  assert.equal(frameAt(once, 0.79), 7);
  assert.equal(frameAt(once, 5), 7);
  assert.equal(finished(once, 0.79), false);
  assert.equal(finished(once, 0.8), true);
  assert.equal(finished(loop, 99), false);
});

test("negative time clamps to frame 0", () => {
  assert.equal(frameAt(loop, -1), 0);
});

test("lean shifts the head most and keeps the feet put", () => {
  assert.deepEqual(leanOffsets(0), [0, 0, 0, 0]);
  assert.deepEqual(leanOffsets(3), [3, 2, 1, 0]);
  assert.equal(leanOffsets(2).at(-1), 0);
});
