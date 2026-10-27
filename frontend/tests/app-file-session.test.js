// The session form: a run and a save read it alike, so the raw EMG a run exports
// and the decomposition saved with it carry the same BIDS entities; the project
// field, the required fields and the hardware pre-fill.
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
  test("a recording in a BIDS dataset sets its placement", () => {
    const session = serviceWith({
      bidsPlacementScheme: "Other",
      bidsPlacementDescription: "",
    });
    session.applyPreviewMetadata({
      metadata: { placement_scheme: "Measured" },
      placement_scheme_description: "measured with a tape",
    });
    const run = session.collectBidsEntities();
    assert.equal(run.placement_scheme, "Measured");
    assert.equal(run.placement_scheme_description, "measured with a tape");
    // A file that records none leaves the form's.
    session.applyPreviewMetadata({ metadata: {} });
    assert.equal(session.collectBidsEntities().placement_scheme, "Measured");
  });

  test("an opened decomposition fills the form and clears the previous file's", () => {
    const state = { edit: {} };
    const els = {};
    for (const id of [
      "bidsSubject",
      "bidsSession",
      "bidsAcquisition",
      "bidsRun",
      "bidsManufacturer",
      "bidsDeviceModel",
      "bidsParticipantAge",
      "bidsParticipantSex",
      "bidsGroundLocation",
      "fsamp",
    ])
      els[id] = { value: "stale" };
    els.bidsTask = { value: "", dataset: {} };
    const session = createFileSessionService(
      /** @type {any} */ ({ els, state, api: {} }),
    );
    session.applySessionInfoFromDecomposition(
      { name: "sub-07_ses-post_task-ramp_acq-hd_decomp.npz" },
      {
        fsamp: 2048.4,
        muscle_names: ["TA"],
        participant_meta: { age: "31", sex: "n/a" },
        manufacturer: "OT Bioelettronica",
        software_versions: "MUedit 2.1.0",
      },
    );
    assert.deepEqual(
      [els.bidsSubject, els.bidsSession, els.bidsTask, els.bidsAcquisition].map(
        (el) => el.value,
      ),
      ["07", "post", "ramp", "hd"],
    );
    assert.equal(els.bidsRun.value, "");
    assert.equal(els.bidsParticipantAge.value, "31");
    assert.equal(els.bidsParticipantSex.value, "");
    assert.equal(els.bidsManufacturer.value, "OT Bioelettronica");
    assert.equal(els.bidsDeviceModel.value, "");
    assert.equal(els.bidsGroundLocation.value, "");
    assert.equal(els.fsamp.value, "2048");
    assert.deepEqual(state.muscleNames, ["TA"]);
    assert.equal(state.fsamp, 2048.4);
    assert.equal(state.edit.softwareVersions, "MUedit 2.1.0");
  });

  test("an unknown power line frequency is sent as unknown, not as 50", () => {
    const session = serviceWith({ bidsPowerlineFreq: "" });
    assert.equal("powerline_freq" in session.collectBidsEntities(), false);
    assert.equal(session.withBidsSaveFields({}).powerline_freq, null);
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

  test("participant, hardware and electrode fields travel with a run and a save", () => {
    const session = serviceWith({
      bidsParticipantAge: "31",
      bidsPowerlineFreq: "60",
      bidsManufacturer: "OT Bioelettronica",
      bidsReferenceLocation: "wet band around the ankle",
      bidsGroundLocation: "wet band around the wrist",
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
    for (const fields of [run, saved]) {
      assert.equal(fields.reference_location, "wet band around the ankle");
      assert.equal(fields.ground_location, "wet band around the wrist");
    }
    assert.equal(
      serviceWith({}).withBidsSaveFields({}).reference_location,
      null,
    );
  });
});

describe("project field", () => {
  globalThis.Option ??= /** @type {any} */ (
    function Option(text, value) {
      return { text, value };
    }
  );

  /** A select over `projects`, then "New project…", and the new-name row. */
  function projectField(projects, listProjects = projects) {
    const options = [...projects, ""].map(
      (value) => new globalThis.Option(value, value),
    );
    const els = {
      bidsProject: {
        value: projects[0] ?? "",
        options,
        add: (option, before) => options.splice(before, 0, option),
        replaceChildren: (...next) => options.splice(0, Infinity, ...next),
      },
      bidsProjectNew: { value: "" },
      bidsProjectNewRow: { hidden: false },
    };
    const state = { edit: { project: "" } };
    const api = { listProjects: async () => ({ projects: listProjects }) };
    const session = createFileSessionService(
      /** @type {any} */ ({ els, state, api }),
    );
    const listed = () => options.map((option) => option.value);
    return {
      session,
      els,
      state,
      listed,
      rowHidden: () => els.bidsProjectNewRow.hidden,
    };
  }

  test("setting it sets the edit session's project too, even to empty", () => {
    const { session, els, state, rowHidden } = projectField(["study1"]);
    session.setBidsProject("study1");
    assert.equal(state.edit.project, "study1");
    assert.ok(rowHidden());
    session.setBidsProject("");
    assert.equal(els.bidsProject.value, "");
    assert.equal(state.edit.project, "");
    assert.equal(rowHidden(), false);
  });

  test("a project missing from the list is added before New project", () => {
    const { session, listed } = projectField(["study1"]);
    session.setBidsProject("study2");
    assert.deepEqual(listed(), ["study1", "study2", ""]);
    assert.equal(session.getBidsProject(), "study2");
  });

  test("New project takes the typed name", () => {
    const { session, els, state } = projectField(["study1"]);
    els.bidsProject.value = "";
    els.bidsProjectNew.value = " study3 ";
    session.syncBidsProject();
    assert.equal(session.getBidsProject(), "study3");
    assert.equal(state.edit.project, "study3");
    els.bidsProject.value = "study1";
    assert.equal(session.getBidsProject(), "study1");
  });

  test("loading lists the output folder's projects and picks one", async () => {
    const { session, listed } = projectField([], ["MUsic", "study1"]);
    await session.loadProjects();
    assert.deepEqual(listed(), ["MUsic", "study1", ""]);
    assert.ok(["MUsic", "study1"].includes(session.getBidsProject()));
  });

  test("an empty output folder starts on New project", async () => {
    const { session, els, rowHidden } = projectField([], []);
    await session.loadProjects();
    assert.equal(els.bidsProject.value, "");
    assert.equal(rowHidden(), false);
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
      bidsProject: "",
      bidsProjectNew: " ",
      muscle: "",
    });
    assert.equal(session.checkSessionForm(), false);
    assert.equal(els.bidsProjectNew.getAttribute("aria-invalid"), "true");
    assert.equal(muscle.getAttribute("aria-invalid"), "true");
    assert.equal(els.bidsSubject.getAttribute("aria-invalid"), null);
    assert.deepEqual(opened, [true]);
    assert.equal(section.classes.has("collapsed"), false);
    assert.equal(section.header.getAttribute("aria-expanded"), "true");
    assert.ok(els.bidsProjectNew.focused);
  });

  test("the Other scheme needs a placement description", () => {
    const { session, els } = formWith({
      ...filled,
      bidsPlacementScheme: "Other",
      bidsPlacementDescription: "",
    });
    assert.equal(session.checkSessionForm(), false);
    assert.equal(session.checkPlacementForm(), false);
    assert.equal(
      els.bidsPlacementDescription.getAttribute("aria-invalid"),
      "true",
    );
    els.bidsPlacementDescription.value = "centred on the muscle belly";
    assert.equal(session.checkPlacementForm(), true);
    assert.equal(
      els.bidsPlacementDescription.getAttribute("aria-invalid"),
      null,
    );
  });

  test("other schemes leave the description optional", () => {
    const { session } = formWith({
      ...filled,
      bidsPlacementScheme: "Measured",
      bidsPlacementDescription: "",
    });
    assert.equal(session.checkSessionForm(), true);
    assert.equal(session.checkPlacementForm(), true);
  });
});

describe("hardware pre-fill", () => {
  test("the file's metadata fills only the hardware fields left empty", () => {
    const els = {
      bidsManufacturer: { value: "" },
      bidsDeviceModel: { value: "Typed by hand" },
      bidsPowerlineFreq: { value: "" },
    };
    const state = {
      metadata: {
        manufacturer: "OT Bioelettronica",
        device_name: "Quattrocento",
        powerline_freq: 60,
      },
    };
    createFileSessionService(
      /** @type {any} */ ({ els, state, api: {} }),
    ).prefillHardwareFields();
    assert.deepEqual(
      [
        els.bidsManufacturer.value,
        els.bidsDeviceModel.value,
        els.bidsPowerlineFreq.value,
      ],
      ["OT Bioelettronica", "Typed by hand", "60"],
    );
  });
});
