// Grid-count inference, and BIDS naming, labels and muscle rows.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  gridColumnCount,
  inferGridCount,
  normalizeGridNames,
} from "../src/io/grid.js";
import {
  buildBidsMuscleRowsModel,
  buildEntityLabelFromSession,
  entityLabelFromFilename,
  getSuggestedNpzName,
  naToEmpty,
  parseBidsEntitiesFromLabel,
} from "../src/io/bids.js";

describe("inferGridCount", () => {
  test("defaults to one grid", () => {
    assert.equal(inferGridCount(), 1);
  });

  test("takes the largest of names, muscles and MU grid indices", () => {
    assert.equal(inferGridCount({ gridNames: ["A", "B"] }), 2);
    assert.equal(inferGridCount({ muscleNames: ["TA", "GM", "SOL"] }), 3);
    assert.equal(inferGridCount({ muGridIndex: [0, 0, 3] }), 4);
  });

  test("ignores negative and non-numeric MU grid indices", () => {
    assert.equal(inferGridCount({ muGridIndex: [-1, "x", 1.7] }), 2);
    assert.equal(inferGridCount({ muGridIndex: [-5, "x"] }), 1);
  });
});

describe("normalizeGridNames", () => {
  test("fills blanks and pads to the minimum count", () => {
    assert.deepEqual(
      normalizeGridNames(["A", "  ", null], { minimumCount: 4 }),
      ["A", "Grid2", "Grid3", "Grid4"],
    );
  });

  test("a non-array gives one default name", () => {
    assert.deepEqual(normalizeGridNames(undefined), ["Grid1"]);
  });

  test("trims names", () => {
    assert.deepEqual(normalizeGridNames([" GR08MM1305 "]), ["GR08MM1305"]);
  });
});

describe("gridColumnCount", () => {
  test("is one past the largest zero-based column", () => {
    const coords = [
      [0, 0],
      [12, 4],
      [3, 1],
    ];
    assert.equal(gridColumnCount(coords), 5);
  });

  test("skips malformed coordinates", () => {
    assert.equal(gridColumnCount([[2], "x", null, [1, 1]]), 2);
    assert.equal(gridColumnCount(null), 1);
  });
});

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

  test("parsing tolerates missing entities and non-strings", () => {
    assert.deepEqual(parseBidsEntitiesFromLabel("sub-01_task-x"), {
      subject: "01",
      session: "",
      task: "x",
      acquisition: "",
      run: "",
    });
    assert.equal(parseBidsEntitiesFromLabel(null).subject, "");
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

describe("naToEmpty", () => {
  test("naToEmpty blanks the BIDS n/a marker", () => {
    assert.equal(naToEmpty("n/a"), "");
    assert.equal(naToEmpty(undefined), "");
    assert.equal(naToEmpty("42"), "42");
  });
});

describe("buildBidsMuscleRowsModel", () => {
  test("outside Edit, the run's grids set the row count", () => {
    const rows = buildBidsMuscleRowsModel({
      currentStage: "run",
      gridNames: ["GR08MM1305", "GR04MM1305"],
      muscleNames: ["TA"],
      edit: { gridNames: [] },
    });
    assert.deepEqual(rows, [
      { id: "bidsMuscle_0", label: "Muscle Grid 1", value: "TA" },
      { id: "bidsMuscle_1", label: "Muscle Grid 2", value: "" },
    ]);
  });

  test("in Edit, the loaded file's grids and MU mapping set the row count", () => {
    const rows = buildBidsMuscleRowsModel({
      currentStage: "edit",
      gridNames: ["run grid"],
      muscleNames: [],
      edit: { gridNames: ["A"], muGridIndex: [0, 2] },
    });
    assert.deepEqual(
      rows.map((r) => r.label),
      ["Muscle Grid 1", "Muscle Grid 2", "Muscle Grid 3"],
    );
  });

  test("with nothing loaded there is one default row", () => {
    const rows = buildBidsMuscleRowsModel({ currentStage: "import" });
    assert.deepEqual(rows, [
      { id: "bidsMuscle_0", label: "Muscle Grid 1", value: "" },
    ]);
  });
});
