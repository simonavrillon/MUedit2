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
