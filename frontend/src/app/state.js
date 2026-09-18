import { GRID_COLORS } from "../config.js";

/** @typedef {import("./context.js").Span} Span */
/** @typedef {import("./context.js").StageKey} StageKey */
/** @typedef {import("./context.js").JsonObject} JsonObject */

/** @typedef {{ name?: string, path?: string }} FileRef */
/** @typedef {Span & { yMin?: number, yMax?: number }} Selection */
/** @typedef {"add" | "add_artifact" | "delete_spikes" | "delete_dr"} EditMode */
/** @typedef {{ muIdx: number, position: number }} Bookmark Where the user last edited an MU. */
/** @typedef {number[] | { min: number[], max: number[] }} ChannelTrace A raw trace, or its min/max envelope. */

/**
 * One MU as it was before the last edit, for a single-step undo.
 *
 * @typedef {object} EditBackup
 * @property {number} muIdx
 * @property {number[]} distimes
 * @property {boolean} flagged
 * @property {number[] | null} pulseTrain
 * @property {number[]} artifactTimes
 */

/**
 * An edit-log entry; saved into the NPZ and read back on load.
 *
 * @typedef {object} EditHistoryEntry
 * @property {string} [type]
 * @property {string} [mu_uid]
 * @property {string} [source_mu_uid]
 * @property {string} [timestamp]
 * @property {number} [view_start]
 * @property {number} [view_end]
 * @property {number[]} [spikes_added]
 * @property {number[]} [spikes_removed]
 * @property {number[]} [artifacts_added]
 * @property {number[]} [artifacts_removed]
 * @property {boolean} [flagged]
 * @property {boolean} [use_peeloff]
 * @property {boolean} [lock_spikes]
 * @property {number} [removed_count]
 * @property {string[]} [removed_mu_uids]
 */

/**
 * @typedef {object} EditSlice
 * @property {FileRef | null} file
 * @property {string} filename
 * @property {number[][]} pulseTrains
 * @property {number[][]} originalPulseTrains
 * @property {number[][]} distimes
 * @property {number[][]} originalDistimes
 * @property {number[][]} artifactTimes
 * @property {string[]} gridNames
 * @property {number[]} muGridIndex
 * @property {number | null} fsamp
 * @property {number} totalSamples
 * @property {number} currentMuGrid
 * @property {number} currentMu
 * @property {Span | null} view
 * @property {Selection | null} selectionPulse
 * @property {Selection | null} selectionDr
 * @property {Selection | null} draftSelectionPulse
 * @property {Selection | null} draftSelectionDr
 * @property {EditMode | null} mode
 * @property {boolean} dirty
 * @property {JsonObject | null} parameters
 * @property {boolean[]} flagged
 * @property {EditBackup | null} backup
 * @property {string} bidsRoot
 * @property {string} project
 * @property {string} editSignalToken
 * @property {string[]} muUids
 * @property {EditHistoryEntry[]} editHistory
 * @property {Bookmark | null} bookmarkPosition
 * @property {boolean} showBookmark
 * @property {string | null} softwareVersions
 */

/**
 * @typedef {object} State
 * @property {FileRef | null} file
 * @property {string | null} uploadToken
 * @property {boolean} isRunning
 * @property {number | null} seriesLength
 * @property {Span[]} rois
 * @property {number[]} previewSeries
 * @property {string[]} gridNames
 * @property {number[][]} gridSeries
 * @property {string[]} gridColors
 * @property {JsonObject | null} parameters
 * @property {number[][]} channelMeans
 * @property {number[][][]} coordinates Per grid, per channel: [row, col].
 * @property {number[][]} discardMasks Per grid, per channel: 1 = discarded.
 * @property {ChannelTrace[][]} channelTraces Per grid, per channel.
 * @property {Record<number, boolean>} qcWindowLoading
 * @property {JsonObject} metadata
 * @property {string[]} muscle
 * @property {StageKey} currentStage
 * @property {number} currentGrid
 * @property {number[][]} muPulseTrains
 * @property {number[][]} muDistimes
 * @property {number[]} muGridIndex
 * @property {number} currentMuGrid
 * @property {number} currentMu
 * @property {boolean} runDownloadInFlight
 * @property {string} lastRunDownloadKey
 * @property {Span | null} runView
 * @property {Span | null} roiDraft
 * @property {Span[]} artifactRegions
 * @property {Span | null} artifactDraft
 * @property {boolean} artifactMode
 * @property {number | null} fsamp
 * @property {number[][]} auxSeries
 * @property {string[]} auxNames
 * @property {EditSlice} edit
 */

// Single source of truth for the edit-slice shape. Used both for the initial
// state below and by resetEditSlice(), so the two can never drift apart.
/** @returns {EditSlice} */
export function createEditSlice() {
  return {
    file: null,
    filename: "",
    pulseTrains: [],
    originalPulseTrains: [],
    distimes: [],
    originalDistimes: [],
    artifactTimes: [],
    gridNames: [],
    muGridIndex: [],
    fsamp: null,
    totalSamples: 0,
    currentMuGrid: 0,
    currentMu: 0,
    view: null,
    selectionPulse: null,
    selectionDr: null,
    draftSelectionPulse: null,
    draftSelectionDr: null,
    mode: null,
    dirty: false,
    parameters: null,
    flagged: [],
    backup: null,
    bidsRoot: "",
    project: "",
    editSignalToken: "",
    muUids: [],
    editHistory: [],
    bookmarkPosition: null,
    showBookmark: false,
    softwareVersions: null,
  };
}

/** @type {State} */
export const state = {
  file: null,
  uploadToken: null,
  isRunning: false,
  seriesLength: null,
  rois: [],
  previewSeries: [],
  gridNames: [],
  gridSeries: [],
  gridColors: GRID_COLORS,
  parameters: null,
  channelMeans: [],
  coordinates: [],
  discardMasks: [],
  channelTraces: [],
  qcWindowLoading: {},
  metadata: {},
  muscle: [],
  currentStage: "qc",
  currentGrid: 0,
  muPulseTrains: [],
  muDistimes: [],
  muGridIndex: [],
  currentMuGrid: 0,
  currentMu: 0,
  runDownloadInFlight: false,
  lastRunDownloadKey: "",
  runView: null,
  roiDraft: null,
  artifactRegions: [],
  artifactDraft: null,
  artifactMode: false,
  fsamp: null,
  auxSeries: [],
  auxNames: [],
  edit: createEditSlice(),
};
