import test from "node:test";
import assert from "node:assert/strict";
import { pickTravel, TRAVEL } from "../src/ride.js";

test("short trips run, medium ones run or skate, long ones hop, skate or fly", () => {
  assert.equal(pickTravel(100, () => 0.99), "run");
  assert.equal(pickTravel(300, () => 0.1), "run");
  assert.equal(pickTravel(300, () => 0.9), "skate");
  assert.deepEqual([0.1, 0.5, 0.9].map((r) => pickTravel(900, () => r)), ["hop", "skate", "rocket"]);
});

test("every choice is a known travel mode", () => {
  for (const len of [10, 200, 800]) for (const r of [0, 0.4, 0.99]) assert.ok(TRAVEL.includes(pickTravel(len, () => r)));
});
