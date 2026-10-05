// A recording 2D context and a minimal DOM, enough to drive the canvas views
// in Node. Not a test file itself: `npm test` only runs tests/*.test.js.
import { readFileSync } from "node:fs";

/** The custom properties of css/tokens.css, with `var()` references resolved. */
function readTokens() {
  const css = readFileSync(
    new URL("../css/tokens.css", import.meta.url),
    "utf8",
  );
  const raw = new Map();
  for (const [, name, value] of css.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    raw.set(name, value.trim());
  }
  const resolve = (value) =>
    value.replace(/var\((--[\w-]+)\)/g, (_, ref) =>
      resolve(raw.get(ref) ?? ""),
    );
  return new Map([...raw].map(([name, value]) => [name, resolve(value)]));
}

const tokens = readTokens();

const CONTEXT_METHODS = [
  "clearRect",
  "fillRect",
  "strokeRect",
  "beginPath",
  "moveTo",
  "lineTo",
  "stroke",
  "fill",
  "arc",
  "fillText",
];

/** Every call is kept with the fill and stroke styles in effect at the time. */
export function fakeContext() {
  const calls = [];
  const ctx = {
    calls,
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    font: "",
    textAlign: "start",
    transform: [1, 0, 0, 1, 0, 0],
    setTransform(...args) {
      ctx.transform = args;
    },
  };
  for (const op of CONTEXT_METHODS) {
    ctx[op] = (...args) =>
      calls.push({
        op,
        args,
        fillStyle: ctx.fillStyle,
        strokeStyle: ctx.strokeStyle,
      });
  }
  return ctx;
}

/** The calls of one kind, e.g. `ops(ctx, "fillText")`. */
export function ops(ctx, op) {
  return ctx.calls.filter((c) => c.op === op);
}

/** The text drawn by `fillText`, in order. */
export function texts(ctx) {
  return ops(ctx, "fillText").map((c) => c.args[0]);
}

/** The `[x, y]` points of every moveTo/lineTo, in order. */
export function pathPoints(ctx) {
  return ctx.calls
    .filter((c) => c.op === "moveTo" || c.op === "lineTo")
    .map((c) => [c.op, ...c.args]);
}

function fakeClassList() {
  const classes = new Set();
  return {
    add: (...names) => names.forEach((n) => classes.add(n)),
    remove: (...names) => names.forEach((n) => classes.delete(n)),
    toggle(name, force = !classes.has(name)) {
      if (force) classes.add(name);
      else classes.delete(name);
      return force;
    },
    contains: (name) => classes.has(name),
  };
}

export function fakeElement(tag = "div") {
  const listeners = {};
  const el = {
    tagName: tag.toUpperCase(),
    children: [],
    style: {},
    dataset: {},
    attributes: {},
    classList: fakeClassList(),
    className: "",
    textContent: "",
    title: "",
    value: "",
    disabled: false,
    get innerHTML() {
      return "";
    },
    set innerHTML(_html) {
      el.children.length = 0;
    },
    appendChild(child) {
      el.children.push(child);
      return child;
    },
    setAttribute(name, value) {
      el.attributes[name] = String(value);
    },
    capturedPointer: null,
    setPointerCapture(id) {
      el.capturedPointer = id;
    },
    addEventListener(type, fn) {
      (listeners[type] ||= []).push(fn);
    },
    dispatch(type, init = {}) {
      for (const fn of listeners[type] || []) {
        fn({ preventDefault() {}, target: el, ...init });
      }
    },
  };
  return el;
}

/** A canvas laid out at `left`/`top` in the page, `width` x `height` CSS px. */
export function fakeCanvas({
  width = 300,
  height = 100,
  left = 0,
  top = 0,
} = {}) {
  const el = fakeElement("canvas");
  const ctx = fakeContext();
  return Object.assign(el, {
    ctx,
    width,
    height,
    clientWidth: width,
    clientHeight: height,
    getContext: () => ctx,
    getBoundingClientRect: () => ({ left, top, width, height }),
  });
}

const windowListeners = {};
const elementsById = {};

/**
 * Install `window` and `document` globals. Call before importing any source
 * module: config.js reads window.location at import time.
 */
export function installDom() {
  globalThis.window = {
    location: {
      port: "8080",
      protocol: "http:",
      hostname: "localhost",
      origin: "http://localhost:8080",
    },
    addEventListener(type, fn) {
      (windowListeners[type] ||= []).push(fn);
    },
    requestAnimationFrame(fn) {
      return setTimeout(() => fn(0), 0);
    },
    setTimeout: () => 0,
  };
  globalThis.getComputedStyle = () => ({
    getPropertyValue: (name) => tokens.get(name) ?? "",
  });
  globalThis.document = {
    documentElement: fakeElement("html"),
    getElementById: (id) => elementsById[id] ?? null,
    createElement: (tag) =>
      tag === "canvas" ? fakeCanvas({ width: 0, height: 0 }) : fakeElement(tag),
  };
}

/** Forget window listeners and registered ids between tests. */
export function resetDom() {
  for (const key of Object.keys(windowListeners)) delete windowListeners[key];
  for (const key of Object.keys(elementsById)) delete elementsById[key];
}

/** Make `document.getElementById(id)` return `el`. */
export function registerElement(id, el) {
  elementsById[id] = el;
}

export function recorder() {
  const calls = [];
  const fn = (...args) => calls.push(args);
  fn.calls = calls;
  return fn;
}
