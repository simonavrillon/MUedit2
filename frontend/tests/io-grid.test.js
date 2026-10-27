// Grid counts, names and column counts, as the catalogue and the backend name them.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  gridColumnCount,
  inferGridCount,
  normalizeGridNames,
} from "../src/io/grid.js";

describe("inferGridCount", () => {
  test("takes the largest of names, muscles and MU grid indices, at least one", () => {
    assert.equal(inferGridCount(), 1);
    assert.equal(inferGridCount({ gridNames: ["A", "B"] }), 2);
    assert.equal(inferGridCount({ muscleNames: ["TA", "GM", "SOL"] }), 3);
    assert.equal(inferGridCount({ muGridIndex: [0, 0, 3] }), 4);
  });
});

describe("normalizeGridNames", () => {
  test("trims names, fills blanks and pads to the minimum count", () => {
    assert.deepEqual(
      normalizeGridNames([" A ", "  ", null], { minimumCount: 4 }),
      ["A", "Grid2", "Grid3", "Grid4"],
    );
  });
});

describe("gridColumnCount", () => {
  test("is one past the largest zero-based column", () => {
    const coords = [
      [0, 0],
      [12, 4],
      [3, 1],
    ];
    assert.equal(gridColumnCount(coords), 5);
  });
});
