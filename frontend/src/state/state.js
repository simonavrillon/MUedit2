/**
 * The app state: its shape (typedefs) and the single `state` object.
 */
/** @typedef {import("../app/context.js").Span} Span */
/** @typedef {import("../app/context.js").StageKey} StageKey */
/** @typedef {import("../app/context.js").JsonObject} JsonObject */

/** @typedef {{ name?: string, path?: string }} FileRef */
/** @typedef {Span & { yMin?: number, yMax?: number }} Selection */
/** @typedef {"add" | "add_artifact" | "delete_spikes"} EditMode */
/** @typedef {{ mu: number, position: number }} Bookmark Where the user last edited an MU. */
/** @typedef {import("../api/binary-payloads.js").SeriesRow} ChannelTrace One row of a viewport: its samples, or their min/max per bin. */
/** @typedef {import("../api/binary-payloads.js").PulseView} PulseView */
/** @typedef {"ramp" | "plateau" | "sinusoid" | "irregular"} SegmentKind */

/**
 * One stretch of the force target, as `/qc/segment` splits it.
 *
 * @typedef {object} TargetSegment
 * @property {number} start
 * @property {number} end
 * @property {SegmentKind} kind
 * @property {"up" | "down" | null} direction Ramps only.
 * @property {number[]} cycles Sinusoid and irregular blocks: where each cycle starts.
 * @property {number | null} frequencyHz Sinusoids only.
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
 * @property {Int32Array[]} spikeTimes
 * @property {Int32Array[]} artifactTimes
 * @property {number[]} versions Per MU; changes whenever the server edits it.
 * @property {string[]} gridNames
 * @property {number[]} muGridIndex
 * @property {number | null} fsamp
 * @property {number} totalSamples
 * @property {number} currentMuGrid
 * @property {number} currentMu
 * @property {Span | null} view
 * @property {Selection | null} selectionPulse
 * @property {Selection | null} draftSelectionPulse
 * @property {EditMode | null} mode
 * @property {boolean} dirty
 * @property {boolean} canUndo
 * @property {boolean} crashRecovery The server logs the edits, so unsaved ones survive a crash.
 * @property {boolean} busy An edit or a save is on its way to the server.
 * @property {JsonObject | null} parameters
 * @property {boolean[]} flagged
 * @property {string} project
 * @property {PulseView | null} pulseView
 * @property {string[]} muUids
 * @property {EditHistoryEntry[]} editHistory The log's tail; older entries stay on the server.
 * @property {number} editHistoryStart Index in the server's log of `editHistory[0]`.
 * @property {Bookmark | null} bookmarkPosition
 * @property {boolean} showBookmark
 * @property {string | null} softwareVersions
 */

/**
 * @typedef {object} State
 * @property {FileRef | null} file
 * @property {string | null} uploadToken
 * @property {boolean} isRunning
 * @property {number | null} totalSamples
 * @property {Span[]} rois
 * @property {string[]} gridNames
 * @property {ChannelTrace[]} gridSeries Per grid, the smoothed mean |EMG| of the whole recording.
 * @property {JsonObject | null} parameters
 * @property {number[][]} channelMeans
 * @property {number[][][]} coordinates Per grid, per channel: [row, col].
 * @property {number[][]} discardMasks Per grid, per channel: 1 = discarded.
 * @property {ChannelTrace[][]} channelTraces Per grid, per channel.
 * @property {JsonObject} metadata
 * @property {string[]} muscleNames
 * @property {StageKey} currentStage
 * @property {number} currentGrid
 * @property {import("../decomp/live.js").RunLive | null} runLive The latest decomposition, as the run page shows it.
 * @property {string} savedRunToken
 * @property {string} runResultToken
 * @property {Span | null} roiDraft
 * @property {Span[]} artifactRegions
 * @property {Span | null} artifactDraft
 * @property {boolean} artifactMode
 * @property {number | null} fsamp
 * @property {ChannelTrace[]} auxSeries Per auxiliary channel, the whole recording.
 * @property {string[]} auxNames
 * @property {TargetSegment[]} segments The target's segments; the chosen ones are those `rois` cover.
 * @property {number | null} targetIndex The auxiliary channel holding the target, when one is known.
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
    spikeTimes: [],
    artifactTimes: [],
    versions: [],
    gridNames: [],
    muGridIndex: [],
    fsamp: null,
    totalSamples: 0,
    currentMuGrid: 0,
    currentMu: 0,
    view: null,
    selectionPulse: null,
    draftSelectionPulse: null,
    mode: null,
    dirty: false,
    canUndo: false,
    crashRecovery: true,
    busy: false,
    parameters: null,
    flagged: [],
    project: "",
    pulseView: null,
    muUids: [],
    editHistory: [],
    editHistoryStart: 0,
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
  totalSamples: null,
  rois: [],
  gridNames: [],
  gridSeries: [],
  parameters: null,
  channelMeans: [],
  coordinates: [],
  discardMasks: [],
  channelTraces: [],
  metadata: {},
  muscleNames: [],
  currentStage: "qc",
  currentGrid: 0,
  runLive: null,
  savedRunToken: "",
  runResultToken: "",
  roiDraft: null,
  artifactRegions: [],
  artifactDraft: null,
  artifactMode: false,
  fsamp: null,
  auxSeries: [],
  auxNames: [],
  segments: [],
  targetIndex: null,
  edit: createEditSlice(),
};
