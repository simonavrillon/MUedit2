/** @typedef {import("../app/context.js").Span} Span */
/** @typedef {import("../app/context.js").StageKey} StageKey */
/** @typedef {import("../app/context.js").JsonObject} JsonObject */

/** @typedef {{ name?: string, path?: string }} FileRef */
/** @typedef {Span & { yMin?: number, yMax?: number }} Selection */
/** @typedef {"add" | "add_artifact" | "delete_spikes" | "delete_dr"} EditMode */
/** @typedef {{ muIdx: number, position: number }} Bookmark Where the user last edited an MU. */
/** @typedef {import("../api/binary-payloads.js").SeriesRow} ChannelTrace One row of a viewport: its samples, or their min/max per bin. */
/** @typedef {import("../api/binary-payloads.js").PulseView} PulseView */

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
 * @property {boolean} [on_save] Logged by the backend for MUs dropped while saving.
 */

/**
 * The edit stage's view of the server-side edit session. The pulse trains
 * stay on the server; `pulseView` holds the window of the current MU on screen.
 *
 * @typedef {object} EditSlice
 * @property {FileRef | null} file
 * @property {string} filename
 * @property {string} token The server's edit session.
 * @property {Int32Array[]} distimes
 * @property {Int32Array[]} artifactTimes
 * @property {number[]} versions Per MU; changes whenever the server edits it.
 * @property {boolean[]} hasPulse Per MU; false when the file has only discharge times.
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
 * @property {boolean} canUndo
 * @property {JsonObject | null} parameters
 * @property {boolean[]} flagged
 * @property {string} bidsRoot
 * @property {string} project
 * @property {PulseView | null} pulseView
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
 * @property {string[]} gridNames
 * @property {ChannelTrace[]} gridSeries Per grid, the smoothed mean |EMG| of the whole recording.
 * @property {JsonObject | null} parameters
 * @property {number[][]} channelMeans
 * @property {number[][][]} coordinates Per grid, per channel: [row, col].
 * @property {number[][]} discardMasks Per grid, per channel: 1 = discarded.
 * @property {ChannelTrace[][]} channelTraces Per grid, per channel.
 * @property {JsonObject} metadata
 * @property {string[]} muscle
 * @property {StageKey} currentStage
 * @property {number} currentGrid
 * @property {import("../decomp/live.js").RunLive | null} runLive The latest decomposition, as the run page shows it.
 * @property {boolean} runDownloadInFlight
 * @property {string} lastRunDownloadKey
 * @property {string} runResultToken
 * @property {Span | null} roiDraft
 * @property {Span[]} artifactRegions
 * @property {Span | null} artifactDraft
 * @property {boolean} artifactMode
 * @property {number | null} fsamp
 * @property {ChannelTrace[]} auxSeries Per auxiliary channel, the whole recording.
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
    token: "",
    distimes: [],
    artifactTimes: [],
    versions: [],
    hasPulse: [],
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
    canUndo: false,
    parameters: null,
    flagged: [],
    bidsRoot: "",
    project: "",
    pulseView: null,
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
  gridNames: [],
  gridSeries: [],
  parameters: null,
  channelMeans: [],
  coordinates: [],
  discardMasks: [],
  channelTraces: [],
  metadata: {},
  muscle: [],
  currentStage: "qc",
  currentGrid: 0,
  runLive: null,
  runDownloadInFlight: false,
  lastRunDownloadKey: "",
  runResultToken: "",
  roiDraft: null,
  artifactRegions: [],
  artifactDraft: null,
  artifactMode: false,
  fsamp: null,
  auxSeries: [],
  auxNames: [],
  edit: createEditSlice(),
};
