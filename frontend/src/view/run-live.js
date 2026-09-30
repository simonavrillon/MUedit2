import {
  DOT,
  RUN_PHASES,
  formatClock,
  formatRemaining,
  keptTotal,
  searchSecondsLeft,
} from "../decomp/live.js";

/** @typedef {import("../app/context.js").Els} Els */
/** @typedef {import("../decomp/live.js").RunLive} RunLive */
/** @typedef {{ label: string, value: string }} PlanItem */

/** Grid colours on the run page: the step colours, extended to ten. */
const GRID_COLOR_COUNT = 10;

/** Dot class per DOT code. */
const DOT_CLASS = ["", "is-kept", "is-rejected", "is-few", "is-skipped"];

/** The run whose dots are on screen; a new run rebuilds them. */
/** @type {RunLive | null} */
let dotsFor = null;

/**
 * @param {HTMLElement | null} list
 * @param {PlanItem[]} items
 */
function renderPairs(list, items) {
  if (!list) return;
  list.replaceChildren(
    ...items.flatMap(({ label, value }) => {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      return [dt, dd];
    }),
  );
}

/** @param {number[]} counts */
function gridCountsText(counts) {
  if (counts.length < 2) return "";
  // By number: grids of one recording often share a model name.
  return counts.map((n, g) => `Grid ${g + 1}: ${n}`).join("   ");
}

/**
 * @param {HTMLElement} el
 * @param {string} cls
 */
function replayAnimation(el, cls) {
  el.classList.remove(cls);
  void el.offsetWidth;
  el.classList.add(cls);
}

/**
 * @param {Els} els
 * @param {RunLive} live
 * @param {string[]} gridNames
 */
function buildDots(els, live, gridNames) {
  const host = els.runDots;
  if (!host) return;
  const nwindows = live.rows.filter((r) => r.grid === 0).length || 1;
  host.replaceChildren(
    ...live.rows.map((row) => {
      const line = document.createElement("div");
      // A grid's first row, after another grid's: spaced off to group each grid's windows.
      line.className =
        row.window === 0 && row.grid > 0 ? "run-row is-grid-start" : "run-row";
      line.style.setProperty(
        "--grid-color",
        `var(--mu-${(row.grid % GRID_COLOR_COUNT) + 1})`,
      );
      const label = document.createElement("span");
      label.className = "run-row-label";
      const grid = `Grid ${row.grid + 1}`;
      label.textContent = nwindows > 1 ? `${grid} · W${row.window + 1}` : grid;
      label.title = gridNames[row.grid] || grid;
      const dots = document.createElement("div");
      dots.className = "run-row-dots";
      for (const code of row.dots) {
        const dot = document.createElement("i");
        dot.className = `run-dot ${DOT_CLASS[code]}`;
        dots.appendChild(dot);
      }
      line.append(label, dots);
      return line;
    }),
  );
  dotsFor = live;
}

/**
 * Restyle the dots one event changed; new kept dots pop.
 *
 * @param {Els} els
 * @param {RunLive} live
 * @param {{ row: number, from: number, to: number }} change
 */
export function updateRunDots(els, live, change) {
  if (dotsFor !== live) return;
  const line = els.runDots?.children[change.row];
  const dots = line?.lastElementChild?.children;
  if (!dots) return;
  const row = live.rows[change.row];
  for (let i = change.from; i < change.to && i < dots.length; i++) {
    const code = row.dots[i];
    dots[i].className =
      `run-dot ${DOT_CLASS[code]}${code === DOT.kept ? " is-new" : ""}`;
  }
}

/**
 * @param {Els} els
 * @param {RunLive | null} live
 * @param {number} now
 */
export function renderRunTime(els, live, now) {
  if (!live) return;
  if (live.status !== "running") {
    const end = live.finishedAt ?? now;
    if (els.runElapsed) {
      els.runElapsed.textContent = `Took ${formatClock((end - live.startedAt) / 1000)}`;
    }
    if (els.runEta) els.runEta.textContent = "";
    return;
  }
  if (els.runElapsed) {
    els.runElapsed.textContent = `Elapsed ${formatClock((now - live.startedAt) / 1000)}`;
  }
  const left = searchSecondsLeft(live, now);
  if (els.runEta) {
    els.runEta.textContent =
      left === null ? "" : `${formatRemaining(left)} left in the search`;
  }
}

/**
 * @param {Els} els
 * @param {RunLive} live
 */
function renderPhases(els, live) {
  const current = RUN_PHASES.findIndex((p) => p.key === live.phase);
  const saveFailed = live.status === "done" && !!live.saveError;
  const finished = live.status === "done" && !live.saving && !saveFailed;
  els.runPhases?.querySelectorAll("li").forEach((li, idx) => {
    const active = idx === current && !finished;
    li.classList.toggle("is-done", finished || idx < current);
    li.classList.toggle(
      "is-active",
      active && (live.status === "running" || live.saving),
    );
    li.classList.toggle(
      "is-failed",
      active && (live.status === "failed" || saveFailed),
    );
  });
}

/**
 * @param {Els} els
 * @param {RunLive} live
 */
function renderCount(els, live) {
  const found = keptTotal(live);
  const summary = live.summary;
  const value = summary ? summary.muCount : found;
  if (els.runCount && els.runCount.textContent !== String(value)) {
    els.runCount.textContent = String(value);
    if (!summary && value > 0) replayAnimation(els.runCount, "is-bumped");
  }
  if (els.runCountLabel) {
    els.runCountLabel.textContent = summary
      ? `motor unit${value === 1 ? "" : "s"} kept`
      : `motor unit${value === 1 ? "" : "s"} found`;
  }
  if (els.runCountGrids) {
    els.runCountGrids.textContent = gridCountsText(
      summary ? summary.perGrid : live.keptByGrid,
    );
  }
}

/**
 * @param {Els} els
 * @param {RunLive} live
 */
function renderResult(els, live) {
  const summary = live.summary;
  /** @type {PlanItem[]} */
  const stats = [];
  if (summary) {
    const found = keptTotal(live);
    if (found > summary.muCount) {
      stats.push({ label: "Found in the search", value: String(found) });
      stats.push({
        label: "Removed in post-processing",
        value: String(found - summary.muCount),
      });
    }
    if (summary.meanSil !== null) {
      stats.push({
        label: "Mean silhouette",
        value: summary.meanSil.toFixed(3),
      });
    }
  }
  renderPairs(els.runResultStats, stats);

  let saveText = "";
  if (live.status === "done") {
    if (live.saving) saveText = "Saving the decomposition…";
    else if (live.savedPath) saveText = `Saved to ${live.savedPath}`;
    else if (live.saveError) saveText = `Save failed: ${live.saveError}`;
    else if (!summary?.muCount) saveText = "No motor units to save.";
  }
  if (els.runSaveText) els.runSaveText.textContent = saveText;
  if (els.runRetrySaveBtn) els.runRetrySaveBtn.hidden = !live.saveError;
  if (els.runError) {
    els.runError.hidden = live.status !== "failed";
    els.runError.textContent = live.error;
  }
}

/**
 * Draw the run page for `live`: the plan before a run, the live search during
 * one, and the result after.
 *
 * @param {Els} els
 * @param {RunLive | null} live
 * @param {{ gridNames: string[], plan: PlanItem[], now: number }} ctx
 */
export function renderRunStage(els, live, ctx) {
  const stage = els.stageRun;
  if (!stage) return;
  if (!live) {
    stage.dataset.mode = "pre";
    renderPairs(els.runPlan, ctx.plan);
    return;
  }
  stage.dataset.mode = live.status === "running" ? "live" : "result";
  if (dotsFor !== live || els.runDots?.childElementCount !== live.rows.length) {
    buildDots(els, live, ctx.gridNames);
  }
  renderPhases(els, live);
  renderCount(els, live);
  renderRunTime(els, live, ctx.now);
  renderResult(els, live);
}
