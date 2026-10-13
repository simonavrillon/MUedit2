// Window arithmetic behind the target segments: union, cut, cover.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  isCovered,
  subtractSpan,
  unionSpans,
} from "../src/signal/intervals.js";

const span = (start, end) => ({ start, end });

describe("unionSpans", () => {
  test("joins spans that touch or overlap, sorted", () => {
    assert.deepEqual(
      unionSpans([span(20, 30), span(0, 10), span(10, 15), span(25, 40)]),
      [span(0, 15), span(20, 40)],
    );
  });

  test("keeps separate spans apart and swallows nested ones", () => {
    assert.deepEqual(unionSpans([span(0, 10), span(2, 5), span(11, 12)]), [
      span(0, 10),
      span(11, 12),
    ]);
  });

  test("drops empty spans and leaves its input alone", () => {
    const input = [span(5, 5), span(0, 3)];
    assert.deepEqual(unionSpans(input), [span(0, 3)]);
    assert.deepEqual(input, [span(5, 5), span(0, 3)]);
  });
});

describe("subtractSpan", () => {
  test("a cut from the middle splits the span", () => {
    assert.deepEqual(subtractSpan([span(0, 30)], span(10, 20)), [
      span(0, 10),
      span(20, 30),
    ]);
  });

  test("a cut at one end shortens it", () => {
    assert.deepEqual(subtractSpan([span(0, 30)], span(20, 30)), [span(0, 20)]);
  });

  test("a cut of the whole span removes it, and others stay", () => {
    assert.deepEqual(subtractSpan([span(0, 10), span(20, 30)], span(0, 10)), [
      span(20, 30),
    ]);
  });

  test("pieces shorter than the minimum are dropped", () => {
    assert.deepEqual(subtractSpan([span(0, 30)], span(2, 25), 4), [
      span(25, 30),
    ]);
  });
});

describe("isCovered", () => {
  test("is true across touching spans and false over a gap", () => {
    assert.equal(isCovered([span(0, 10), span(10, 20)], span(5, 15)), true);
    assert.equal(isCovered([span(0, 10), span(11, 20)], span(5, 15)), false);
    assert.equal(isCovered([], span(0, 1)), false);
  });
});
