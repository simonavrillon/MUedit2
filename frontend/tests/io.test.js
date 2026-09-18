// Grid-count inference and BIDS naming, labels and metadata models.
import { test, describe } from "node:test";
import assert from "node:assert/strict";

import {
  gridDimensionsFor,
  inferGridCount,
  normalizeGridNames,
} from "../src/io/grid.js";
import {
  buildBidsAutoInfoModel,
  buildBidsMuscleRowsModel,
  buildEntityLabelFromSession,
  buildSessionInfoFromDecomposition,
  getSuggestedNpzName,
  listifyMuscles,
  naToEmpty,
  parseBidsEntitiesFromLabel,
} from "../src/io/bids.js";

describe("inferGridCount", () => {
  test("defaults to one grid", () => {
    assert.equal(inferGridCount(), 1);
    assert.equal(inferGridCount({ minimum: 0 }), 1);
  });

  test("takes the largest of names, muscles and MU grid indices", () => {
    assert.equal(inferGridCount({ gridNames: ["A", "B"] }), 2);
    assert.equal(inferGridCount({ muscles: ["TA", "GM", "SOL"] }), 3);
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
      ["A", "Grid 2", "Grid 3", "Grid 4"],
    );
  });

  test("a non-array gives one default name", () => {
    assert.deepEqual(normalizeGridNames(undefined), ["Grid 1"]);
  });

  test("trims names", () => {
    assert.deepEqual(normalizeGridNames([" GR08MM1305 "]), ["GR08MM1305"]);
  });
});

describe("gridDimensionsFor", () => {
  test("rows and columns are one past the largest zero-based coordinate", () => {
    const coords = [
      [0, 0],
      [12, 4],
      [3, 1],
    ];
    assert.deepEqual(gridDimensionsFor(coords), { rows: 13, cols: 5 });
  });

  test("skips malformed coordinates", () => {
    assert.deepEqual(gridDimensionsFor([[2], "x", null, [1, 1]]), {
      rows: 2,
      cols: 2,
    });
    assert.deepEqual(gridDimensionsFor(null), { rows: 1, cols: 1 });
  });
});

describe("BIDS entity labels", () => {
  test("emits entities in canonical order and omits empty ones", () => {
    assert.equal(
      buildEntityLabelFromSession({
        subject: "03",
        session: "pre",
        task: "ramp",
        acq: "hdemg",
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
      acq: "hdemg",
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
      acq: "",
      run: "",
    });
    assert.equal(parseBidsEntitiesFromLabel(null).subject, "");
  });

  test("an entity key inside another word is not matched", () => {
    assert.equal(parseBidsEntitiesFromLabel("mysub-99_task-x").subject, "");
  });
});

describe("getSuggestedNpzName", () => {
  test("swaps the extension for the suffix", () => {
    assert.equal(
      getSuggestedNpzName("sub-01_task-ramp.npz"),
      "sub-01_task-ramp_edited.npz",
    );
  });

  test("does not stack the suffix on a re-save", () => {
    assert.equal(getSuggestedNpzName("run_edited.npz"), "run_edited.npz");
  });

  test("defaults the stem and honours an empty suffix", () => {
    assert.equal(getSuggestedNpzName(""), "decomposition_edited.npz");
    assert.equal(getSuggestedNpzName("run.mat", ""), "run.npz");
  });
});

describe("muscles and metadata", () => {
  test("listifyMuscles accepts a list or a single name", () => {
    assert.deepEqual(listifyMuscles([" TA ", "", null, "GM"]), ["TA", "GM"]);
    assert.deepEqual(listifyMuscles(" VL "), ["VL"]);
    assert.deepEqual(listifyMuscles("  "), []);
    assert.deepEqual(listifyMuscles(3), []);
  });

  test("naToEmpty blanks the BIDS n/a marker", () => {
    assert.equal(naToEmpty("n/a"), "");
    assert.equal(naToEmpty(undefined), "");
    assert.equal(naToEmpty("42"), "42");
  });

  test("the auto-info panel hides with no hardware metadata", () => {
    assert.deepEqual(buildBidsAutoInfoModel({ metadata: {}, muscle: [] }), {
      hidden: true,
    });
  });

  test("the auto-info panel shows the first filter and gain, unique muscles", () => {
    const model = buildBidsAutoInfoModel({
      metadata: {
        manufacturer: "OT Bioelettronica",
        device_name: "Quattrocento",
        emg_hpf: [10, 10],
        emg_lpf: 500,
        gains: [150, 150],
      },
      muscle: ["TA", "TA", " ", "GM"],
    });
    assert.deepEqual(model, {
      hidden: false,
      manufacturer: "OT Bioelettronica",
      deviceName: "Quattrocento",
      musclesText: "TA, GM",
      filtersText: "10 - 500 Hz",
      gainText: "150",
    });
  });

  test("a missing filter edge reads n/a", () => {
    const model = buildBidsAutoInfoModel({
      metadata: { device_name: "X", emg_lpf: 500 },
    });
    assert.equal(model.filtersText, "n/a - 500 Hz");
  });
});

describe("buildBidsMuscleRowsModel", () => {
  test("outside Edit, the run's grids label the rows", () => {
    const rows = buildBidsMuscleRowsModel({
      currentStage: "run",
      gridNames: ["GR08MM1305", "GR04MM1305"],
      muscle: ["TA"],
      edit: { gridNames: [] },
    });
    assert.deepEqual(rows, [
      { id: "bidsMuscle_0", label: "Muscle Grid 1 (GR08MM1305)", value: "TA" },
      { id: "bidsMuscle_1", label: "Muscle Grid 2 (GR04MM1305)", value: "" },
    ]);
  });

  test("in Edit, the loaded file's grids and MU mapping set the row count", () => {
    const rows = buildBidsMuscleRowsModel({
      currentStage: "edit",
      gridNames: ["run grid"],
      muscle: [],
      edit: { gridNames: ["A"], muGridIndex: [0, 2] },
    });
    assert.deepEqual(
      rows.map((r) => r.label),
      ["Muscle Grid 1 (A)", "Muscle Grid 2 (Grid 2)", "Muscle Grid 3 (Grid 3)"],
    );
  });

  test("with nothing loaded there is one default row", () => {
    const rows = buildBidsMuscleRowsModel({ currentStage: "import" });
    assert.deepEqual(rows, [
      { id: "bidsMuscle_0", label: "Muscle Grid 1 (Grid 1)", value: "" },
    ]);
  });
});

describe("buildSessionInfoFromDecomposition", () => {
  const deps = { parseBidsEntitiesFromLabel, listifyMuscles };

  test("reads entities from the file name and muscles from the payload", () => {
    const info = buildSessionInfoFromDecomposition(
      { name: "sub-07_ses-post_task-ramp_decomp.npz" },
      {
        fsamp: 2048.4,
        muscle: ["TA"],
        parameters: { target_muscle: "GM" },
        participant_meta: { age: "31", sex: "n/a" },
        manufacturer: "OT Bioelettronica",
        powerline_freq: 50,
        software_versions: "MUedit 2.1.0",
      },
      deps,
    );
    assert.equal(info.fileLabel, "sub-07_ses-post_task-ramp_decomp.npz");
    assert.equal(info.fsampText, "2048");
    assert.equal(info.entities.subject, "07");
    assert.equal(info.entities.session, "post");
    assert.deepEqual(info.muscles, ["TA"]);
    assert.deepEqual(info.participant, { age: "31", sex: "", handedness: "" });
    assert.equal(info.hardware.manufacturer, "OT Bioelettronica");
    assert.equal(info.bids.powerlineFreq, 50);
    assert.equal(info.bids.softwareVersions, "MUedit 2.1.0");
  });

  test("falls back to the target muscle parameter and the payload label", () => {
    const info = buildSessionInfoFromDecomposition(
      null,
      { file_label: "sub-02_task-x", parameters: { target_muscle: "GM" } },
      deps,
    );
    assert.equal(info.fileLabel, "sub-02_task-x");
    assert.deepEqual(info.muscles, ["GM"]);
    assert.equal(info.fsampText, "");
    assert.equal(info.bids.softwareVersions, null);
  });
});
