# MUnitQuest EMG-BIDS example (sidecars only)

Sidecar files copied unchanged from the MUnitQuest EMG-BIDS tutorial dataset,
<https://github.com/MUnitQuest/MUnitQuest_tutorials/tree/13af32d/emg_bids_tutorial1/MUnitQuest_BIDS_Tutorial>
(commit `13af32d`), under the MIT licence in `LICENSE`.

- `sub-01`: one 3x4 surface grid (`grid1`) and one intramuscular wire (`intramuscular`), plus a
  torque channel; the grid is anchored to the `thigh` space.
- `sub-02/ses-01`: two 3x3 surface grids (`grid1`, `grid2`) and a torque channel.
- Both recordings keep their `events.tsv` (task phases in `event_type`: `muscle_on`,
  `linear_ramp`, `steady_hold`, `muscle_off`).

The EDF recordings are not copied: `tests/test_bids_interop.py` writes short ones with the
channel labels of each `channels.tsv`.
The tutorial's grids are not in MUedit's catalogue, so the tests add catalogue entries and a
`grid_name` column first, as a user would.
