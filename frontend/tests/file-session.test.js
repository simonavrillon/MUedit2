// A run and a save read the session form alike, so the raw EMG a run exports
// and the decomposition saved with it carry the same BIDS entities.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

const { createFileSessionService } =
  await import("../src/app/services/file-session.js");

/** @param {Record<string, string>} values */
function serviceWith(values) {
  /** @type {Record<string, { value: string }>} */
  const els = {};
  for (const [id, value] of Object.entries(values)) els[id] = { value };
  return createFileSessionService(
    /** @type {any} */ ({ els, state: {}, api: {} }),
  );
}

describe("session form", () => {
  test("a run sends the acquisition with the other entities", () => {
    const session = serviceWith({
      bidsSubject: "01",
      bidsTask: "mvc",
      bidsAcquisition: "ramp",
    });
    assert.deepEqual(session.collectBidsEntities(), {
      subject: "01",
      task: "mvc",
      acquisition: "ramp",
      powerline_freq: 50,
      placement_scheme: "ChannelSpecific",
    });
  });

  test("a run and a save clean the labels the same way", () => {
    const session = serviceWith({
      bidsSubject: "S 01",
      bidsTask: "ramp-up",
      bidsAcquisition: "hd emg",
      bidsRun: "2",
    });
    const run = session.collectBidsEntities();
    assert.deepEqual(
      [run.subject, run.task, run.acquisition, run.run],
      ["S01", "rampup", "hdemg", "2"],
    );
    const saved = session.withBidsSaveFields({ entity_label: "x" });
    assert.equal(saved.entity_label, "sub-S01_task-rampup_acq-hdemg_run-2");
  });

  test("participant and hardware fields travel with both", () => {
    const session = serviceWith({
      bidsParticipantAge: "31",
      bidsPowerlineFreq: "60",
      bidsManufacturer: "OT Bioelettronica",
    });
    const run = session.collectBidsEntities();
    const saved = session.withBidsSaveFields({});
    const meta = { age: "31", sex: "n/a", handedness: "n/a" };
    assert.deepEqual(run.participant_meta, meta);
    assert.deepEqual(saved.participant_meta, meta);
    assert.equal(run.powerline_freq, 60);
    assert.equal(saved.powerline_freq, 60);
    assert.equal(run.manufacturer, "OT Bioelettronica");
    assert.equal(saved.manufacturer, "OT Bioelettronica");
    assert.equal(saved.manufacturers_model_name, null);
    assert.equal("manufacturers_model_name" in run, false);
  });
});

describe("project field", () => {
  test("setting it sets the edit session's project too, even to empty", () => {
    const els = { bidsProject: { value: "old" } };
    const state = { edit: { project: "old" } };
    const session = createFileSessionService(
      /** @type {any} */ ({ els, state, api: {} }),
    );
    session.setBidsProject("study-1");
    assert.equal(els.bidsProject.value, "study-1");
    assert.equal(state.edit.project, "study-1");
    session.setBidsProject("");
    assert.equal(els.bidsProject.value, "");
    assert.equal(state.edit.project, "");
  });
});

describe("required session fields", () => {
  /** A field that keeps its attributes, inside one panel section. */
  function field(value, section) {
    const attrs = new Map();
    return {
      value,
      focused: false,
      setAttribute: (name, v) => attrs.set(name, v),
      removeAttribute: (name) => attrs.delete(name),
      getAttribute: (name) => attrs.get(name) ?? null,
      closest: () => section,
      focus() {
        this.focused = true;
      },
    };
  }

  function sessionPanel() {
    const header = field("", null);
    const classes = new Set(["panel-section", "collapsed"]);
    return {
      header,
      classes,
      classList: { remove: (c) => classes.delete(c) },
      querySelector: () => header,
    };
  }

  function formWith(values) {
    const section = sessionPanel();
    /** @type {Record<string, any>} */
    const els = {};
    for (const [id, value] of Object.entries(values)) {
      els[id] = field(value, section);
    }
    const muscle = field(values.muscle ?? "", section);
    els.bidsMuscleContainer = { querySelectorAll: () => [muscle] };
    const opened = [];
    const session = createFileSessionService(
      /** @type {any} */ ({
        els,
        state: {},
        api: {},
        setSettingsOpen: (open) => opened.push(open),
      }),
    );
    return { session, els, muscle, section, opened };
  }

  const filled = {
    bidsProject: "study1",
    bidsSubject: "1",
    bidsSession: "1",
    bidsTask: "trapezoid",
    bidsParticipantAge: "31",
    bidsParticipantSex: "F",
    bidsParticipantHandedness: "right",
    bidsManufacturer: "OT Bioelettronica",
    bidsDeviceModel: "Quattrocento",
    muscle: "TA",
  };

  test("a filled form passes and leaves the panel alone", () => {
    const { session, opened, section } = formWith(filled);
    assert.equal(session.checkSessionForm(), true);
    assert.deepEqual(opened, []);
    assert.ok(section.classes.has("collapsed"));
  });

  test("empty fields are marked and the panel opens on the first", () => {
    const { session, els, muscle, section, opened } = formWith({
      ...filled,
      bidsProject: " ",
      muscle: "",
    });
    assert.equal(session.checkSessionForm(), false);
    assert.equal(els.bidsProject.getAttribute("aria-invalid"), "true");
    assert.equal(muscle.getAttribute("aria-invalid"), "true");
    assert.equal(els.bidsSubject.getAttribute("aria-invalid"), null);
    assert.deepEqual(opened, [true]);
    assert.equal(section.classes.has("collapsed"), false);
    assert.equal(section.header.getAttribute("aria-expanded"), "true");
    assert.ok(els.bidsProject.focused);
  });
});
