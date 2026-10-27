// BIDS naming: entity labels, suggested file names and the muscle rows.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  buildBidsMuscleRowsModel,
  buildEntityLabelFromSession,
  entityLabelFromFilename,
  getSuggestedNpzName,
  parseBidsEntitiesFromLabel,
} from "../src/io/bids.js";

describe("BIDS entity labels", () => {
  test("emits entities in canonical order and omits empty ones", () => {
    assert.equal(
      buildEntityLabelFromSession({
        subject: "03",
        session: "pre",
        task: "ramp",
        acquisition: "hdemg",
        run: "02",
      }),
      "sub-03_ses-pre_task-ramp_acq-hdemg_run-02",
    );
    assert.equal(
      buildEntityLabelFromSession({ subject: "03", task: "ramp" }),
      "sub-03_task-ramp",
    );
  });

  test("strips characters BIDS labels cannot hold, with defaults", () => {
    assert.equal(
      buildEntityLabelFromSession({ subject: "P-01 ", task: "iso_30%" }),
      "sub-P01_task-iso30",
    );
    assert.equal(buildEntityLabelFromSession(), "sub-01_task-task");
    assert.equal(
      buildEntityLabelFromSession({ subject: "--" }),
      "sub-01_task-task",
    );
  });

  test("parsing a built label gives back its entities", () => {
    const entities = {
      subject: "03",
      session: "pre",
      task: "ramp",
      acquisition: "hdemg",
      run: "02",
    };
    const label = buildEntityLabelFromSession(entities);
    assert.deepEqual(parseBidsEntitiesFromLabel(`${label}_emg.edf`), entities);
  });

  test("missing entities parse as empty", () => {
    assert.deepEqual(parseBidsEntitiesFromLabel("sub-01_task-x"), {
      subject: "01",
      session: "",
      task: "x",
      acquisition: "",
      run: "",
    });
  });

  test("an entity key inside another word is not matched", () => {
    assert.equal(parseBidsEntitiesFromLabel("mysub-99_task-x").subject, "");
  });

  test("a recording's file name gives its label without _emg", () => {
    assert.equal(
      entityLabelFromFilename("sub-01_task-ramp_emg.edf"),
      "sub-01_task-ramp",
    );
    assert.equal(entityLabelFromFilename("sub-01_task-x.bdf"), "sub-01_task-x");
  });
});

describe("getSuggestedNpzName", () => {
  test("swaps the extension for the suffix", () => {
    assert.equal(
      getSuggestedNpzName("sub-01_task-ramp.npz", "_edited"),
      "sub-01_task-ramp_edited.npz",
    );
  });

  test("does not stack the suffix on a re-save", () => {
    assert.equal(
      getSuggestedNpzName("run_edited.npz", "_edited"),
      "run_edited.npz",
    );
  });
});

describe("buildBidsMuscleRowsModel", () => {
  test("outside Edit, the run's grids set the row count", () => {
    const rows = buildBidsMuscleRowsModel({
      currentStage: "run",
      gridNames: ["GR08MM1305", "GR04MM1305"],
      muscleNames: ["TA"],
    });
    assert.deepEqual(rows, [
      { id: "bidsMuscle_0", label: "Muscle Grid 1", value: "TA" },
      { id: "bidsMuscle_1", label: "Muscle Grid 2", value: "" },
    ]);
  });

  test("in Edit, the loaded file's grids and MU mapping set the row count", () => {
    const rows = buildBidsMuscleRowsModel({
      currentStage: "edit",
      gridNames: ["A"],
      muscleNames: [],
      edit: { token: "t", muGridIndex: [0, 2] },
    });
    assert.deepEqual(
      rows.map((r) => r.label),
      ["Muscle Grid 1", "Muscle Grid 2", "Muscle Grid 3"],
    );
  });

  test("with nothing loaded there is one default row", () => {
    const rows = buildBidsMuscleRowsModel({
      currentStage: "import",
      gridNames: [],
      muscleNames: [],
      edit: { token: "", muGridIndex: [] },
    });
    assert.deepEqual(rows, [
      { id: "bidsMuscle_0", label: "Muscle Grid 1", value: "" },
    ]);
  });
});
