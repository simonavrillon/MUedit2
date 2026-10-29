# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.2.1] - 2026-10-29

### Added

- A **Projects** section at the top of the settings panel lists the output folder's projects
  as a tree with folder icons, replacing the **Project** dropdown. **Change output folder**
  under it picks another folder of projects, and MUedit remembers it for the next launch. Both
  are disabled while an opened decomposition is being edited, so it saves back where it came
  from.

## [2.2.0] - 2026-10-27

### Added

- Intan `.rhd` and Open Ephys binary recordings load directly.
- **Automatic QC** on the QC page flags bad channels and artifact regions.
- **Segment target** splits a recorded force/torque target into segments, and the
  **Segment types** button ticks every segment of a type at once.
- A **Full trace** post-processing mode extends units beyond the analysis window.
- Trigger pulses and target phases are saved to BIDS events files and read back on load.
- Saved `.npz` files record the MUedit version that wrote them, as the BIDS export already
  does.

### Changed

- Setup runs through `uv`; the conda environment is gone.
- One `muedit` command replaces the separate command-line entry points.
- The Decompose page follows the run live instead of showing a progress bar.
- Decompositions save as schema v2 `.npz` files, which load without pickle. Older v1 files
  still open.

### Removed

- Uploading recordings through the browser. The app opens the file by path instead.

### Fixed

- A crash on long recordings.

### Security

- A local file read vulnerability through the API.

[Unreleased]: https://github.com/simonavrillon/MUedit2/compare/v2.2.1...HEAD
[2.2.1]: https://github.com/simonavrillon/MUedit2/releases/tag/v2.2.1
[2.2.0]: https://github.com/simonavrillon/MUedit2/releases/tag/v2.2.0
