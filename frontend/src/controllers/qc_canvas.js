export function syncRois(state, nwin) {
  if (!state.rois) state.rois = [];
  if (state.rois.length > nwin) state.rois = state.rois.slice(0, nwin);
  while (state.rois.length < nwin) {
    state.rois.push({ start: 0, end: state.seriesLength || 0 });
  }
}

export function refreshVisuals({
  state,
  els,
  drawGridOverlay,
  renderAuxiliaryChannels,
  renderMuExplorer,
}) {
  const selections = state.roiDraft
    ? [...(state.rois || []), state.roiDraft]
    : state.rois;
  const emgCanvas = els?.emgCanvas || "emgCanvas";
  drawGridOverlay(
    emgCanvas,
    state.gridSeries,
    state.gridColors,
    selections,
    state.seriesLength,
  );
  renderAuxiliaryChannels();
  renderMuExplorer();
}

export function enableRoiSelection(
  {
    state,
    els,
    syncRoisFn,
    refreshVisualsFn,
    requestQcGridWindowFn,
    updateProgressFn,
  },
  canvasId,
) {
  const canvas = els?.[canvasId] || document.getElementById(canvasId);
  if (!canvas || canvas.dataset.roiBound === "1") return;
  canvas.dataset.roiBound = "1";

  let dragging = false;
  let startX = 0;
  let endX = 0;

  const toSamples = (sx, ex) => {
    const width = canvas.clientWidth || 1;
    const s = Math.max(0, Math.min(width, Math.min(sx, ex)));
    const e = Math.max(0, Math.min(width, Math.max(sx, ex)));
    const startSample = Math.round((s / width) * state.seriesLength);
    const endSample = Math.round((e / width) * state.seriesLength);
    return { startSample, endSample };
  };

  const commitSelection = () => {
    if (!state.seriesLength) return;
    const { startSample, endSample } = toSamples(startX, endX);
    const nwin = Number(els.nwindows?.value) || 1;
    syncRoisFn(nwin);
    let idx = 0;
    let best = Number.MAX_SAFE_INTEGER;
    state.rois.forEach((r, i) => {
      const dist = Math.abs(r.start - startSample);
      if (dist < best) {
        best = dist;
        idx = i;
      }
    });
    state.rois[idx] = {
      start: startSample,
      end: Math.max(startSample + 1, endSample),
    };
    state.roiDraft = null;
    state.channelTraces = [];
    refreshVisualsFn();
    requestQcGridWindowFn(
      state.currentGrid,
      state.rois[0]?.start || 0,
      state.rois[0]?.end || state.seriesLength,
    );
    updateProgressFn(
      undefined,
      `ROI updated (${state.rois.length} window${state.rois.length > 1 ? "s" : ""})`,
    );
  };

  canvas.addEventListener("mousedown", (e) => {
    if (!state.seriesLength) return;
    dragging = true;
    const rect = canvas.getBoundingClientRect();
    startX = e.clientX - rect.left;
    endX = startX;
    state.roiDraft = null;
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!dragging || !state.seriesLength) return;
    const rect = canvas.getBoundingClientRect();
    endX = e.clientX - rect.left;
    const { startSample, endSample } = toSamples(startX, endX);
    state.roiDraft = {
      start: startSample,
      end: Math.max(startSample + 1, endSample),
    };
    refreshVisualsFn();
  });

  window.addEventListener("mouseup", () => {
    if (!dragging || !state.seriesLength) {
      dragging = false;
      return;
    }
    dragging = false;
    if (Math.abs(endX - startX) < 4) {
      state.roiDraft = null;
      refreshVisualsFn();
      return;
    }
    commitSelection();
  });
}

export function renderChannelQC(
  {
    state,
    els,
    nextFrame,
    drawMiniSeries,
    requestQcGridWindow,
    getCurrentGrid,
    ensureDiscardMasks,
  },
  waitForMiniPlots = false,
) {
  const section = els.qcSection;
  if (!section) return waitForMiniPlots ? Promise.resolve() : undefined;
  section.innerHTML = "";
  ensureDiscardMasks();
  let gridIdx = getCurrentGrid();
  const allMeans = state.channelMeans || [];
  if (!allMeans[gridIdx] && allMeans.length) {
    gridIdx = 0;
    state.currentGrid = 0;
  }
  const means = allMeans[gridIdx];
  if (!means) return waitForMiniPlots ? Promise.resolve() : undefined;
  const meanList = Array.isArray(means) ? means : Array.from(means || []);
  if (!meanList.length) return waitForMiniPlots ? Promise.resolve() : undefined;
  const wrap = document.createElement("div");
  wrap.className = "qc-grid";

  const cells = document.createElement("div");
  cells.className = "cells";
  const coords = state.coordinates?.[gridIdx] || [];
  let maxRow = 0;
  let maxCol = 0;
  coords.forEach((c) => {
    if (Array.isArray(c) && c.length >= 2) {
      maxRow = Math.max(maxRow, c[0]);
      maxCol = Math.max(maxCol, c[1]);
    }
  });
  const cols = (maxCol || 0) + 1;
  cells.style.gridTemplateColumns = `repeat(${cols}, minmax(26px, 1fr))`;

  const mask = state.discardMasks?.[gridIdx] || [];
  const traces = state.channelTraces?.[gridIdx] || [];
  const miniDrawJobs = [];
  if (!traces.length) {
    const roi = state.rois?.[0];
    requestQcGridWindow(
      gridIdx,
      Number.isFinite(roi?.start) ? roi.start : 0,
      Number.isFinite(roi?.end) ? roi.end : state.seriesLength,
    );
  }
  meanList.forEach((val, chIdx) => {
    const pos = coords[chIdx] || [];
    const r = pos[0] ?? Math.floor(chIdx / cols);
    const c = pos[1] ?? chIdx % cols;
    const cell = document.createElement("button");
    cell.className = "qc-cell";
    cell.style.gridRow = `${r + 1}`;
    cell.style.gridColumn = `${c + 1}`;
    const off = mask[chIdx] === 1;
    if (off) cell.classList.add("off");
    const label = document.createElement("div");
    label.textContent = chIdx + 1;
    label.className = "qc-label";
    const mini = document.createElement("canvas");
    mini.height = 26;
    mini.className = "qc-mini";
    cell.appendChild(label);
    cell.appendChild(mini);
    const meanVal = Number(val);
    const meanText = Number.isFinite(meanVal) ? meanVal.toFixed(3) : "n/a";
    cell.title = `Channel ${chIdx + 1} • mean |EMG| ${meanText}`;
    cell.addEventListener("click", () => {
      mask[chIdx] = off ? 0 : 1;
      state.discardMasks[gridIdx] = mask;
      renderChannelQC(
        {
          state,
          els,
          nextFrame,
          drawMiniSeries,
          requestQcGridWindow,
          getCurrentGrid,
          ensureDiscardMasks,
        },
        false,
      );
    });
    miniDrawJobs.push(() =>
      drawMiniSeries(mini, traces[chIdx], mask[chIdx] === 1),
    );
    cells.appendChild(cell);
  });

  wrap.appendChild(cells);
  section.appendChild(wrap);
  const runMiniDraw = () => {
    miniDrawJobs.forEach((job) => job());
  };
  if (waitForMiniPlots) {
    return nextFrame().then(() => {
      runMiniDraw();
      return nextFrame();
    });
  }
  setTimeout(runMiniDraw, 0);
  return undefined;
}
