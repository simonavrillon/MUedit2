# MUedit

MUedit decomposes electromyographic signals recorded with multi-channel arrays
(surface or intramuscular) into motor unit pulse trains. The peaks of these
pulse trains represent the discharge times of the motor units. MUedit lets you
i) interactively visualise your signals and remove noisy channels,
ii) automatically decompose your files, and iii) inspect and manually edit the
output of the decomposition.

It runs as a small app in your browser. Nothing is uploaded anywhere: the
interface is a local web page, the computation happens on your own machine, and
your recordings and results stay in the folders you choose.

MUedit is a research tool, actively used and actively maintained. It works, and
we keep extending it as our own projects need it to do more. The adaptive
decomposition option is newer than the rest and still being tuned, so treat it
as experimental. If something is awkward or missing, we would rather hear about
it than not.

## Before your first run

You need one thing installed: [uv](https://docs.astral.sh/uv/), a Python
project manager. It downloads its own copy of Python, so you do not need to
have Python set up, and it will not touch anything else on your system.

On macOS or Linux, open a terminal and run:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows, open PowerShell and run:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then close that window and open a new one, so it picks up the freshly installed
`uv`. Move into the MUedit folder and install everything MUedit needs.

On macOS or Linux:

```bash
cd path/to/MUedit
uv sync
```

On Windows (PowerShell):

```powershell
cd path\to\MUedit
uv sync
```

This reads a locked list of exact package versions, so you get the same working
setup as everyone else, and it only has to be done once. It takes a couple of
minutes the first time.

## Starting the app

Open the MUedit folder and double-click **MUedit** — `MUedit.command` on macOS,
`MUedit.bat` on Windows. A terminal window appears and reports what it is doing;
leave it open, because that window *is* MUedit running.

Your browser should open on MUedit's landing page by itself. If it does not, go
to <http://localhost:8080> — the app is already running and waiting for you.

When you are finished, quit MUedit from that terminal window rather than the
browser: press `Ctrl+C` in it, or simply close the window. Either one stops the
app cleanly. Closing the browser tab on its own does **not** quit MUedit — the
tab is only a window onto the app, which keeps running in the background and
holding on to its ports. If you close the tab by accident, nothing is lost:
reopen <http://localhost:8080> and carry on where you were.

Two things to know the first time. On macOS, if you downloaded MUedit rather
than cloning it, macOS will refuse to open the launcher from an unidentified
developer; right-click **MUedit.command**, choose **Open**, and confirm once.
Every double-click after that works normally. On Windows, nothing special is
needed — the `.bat` launcher already tells PowerShell to allow this one script,
so you do not have to change any system-wide setting.

If you prefer a terminal, or you are on Linux, the same launchers live in
`scripts/` and can be run directly:

```bash
./scripts/run_MUedit.sh          # macOS / Linux
.\scripts\run_MUedit.ps1         # Windows PowerShell
```

## Working with a recording

A session in MUedit moves through four stages, shown as buttons across the top
of the window. They unlock in order as you go: import your signal, check its
quality and choose the part you care about, decompose it, then edit the result.

### Opening your file

Click the folder icon on the landing page and pick your recording. MUedit reads
the common high-density EMG formats directly, so in most cases there is no
conversion step:

| Recording from | File to open |
|---|---|
| MATLAB | `.mat` (both v5 and v7.3) |
| OT Biolab+ | `.otb+` |
| OT Biolab 4 | `.otb4` |
| Intan | `.rhd` file, or the recording folder |
| BIDS EMG | `.bdf` or `.edf`, with its `_channels.tsv` sidecar beside it |
| A previous MUedit session | `.npz` — opens straight into the editor |

For Intan recordings you can point either at the `.rhd` file or at the folder,
whichever your acquisition software produced; MUedit works out the layout and
groups the amplifier channels into one grid per port. For BIDS recordings, the
`_channels.tsv` sidecar next to the data file tells MUedit which channels belong
to which grid, and which are auxiliary signals such as force.

Once the file is read, MUedit moves you on to the quality-check stage.

### Checking the signal and choosing a window

This stage is where you decide what is worth decomposing. You will see the
electrode grid laid out as tiles, the average EMG activity over the whole
recording, and any auxiliary channels such as force or torque.

The quickest way in is the **Automatic QC** button next to the grid tabs. It
looks for channels that are flat, saturated, quantised, noisy, or losing
contact, and for stretches of time contaminated by artifacts, and marks what it
finds. Nothing is decided yet — click any tile to disagree with it, and review
the artifact regions before moving on.

Then choose your region of interest by dragging across the average EMG chart or
the auxiliary channel panel. Decomposition will run on that window only, which
both saves time and keeps rest periods from diluting the result. If a short
burst of noise sits inside the window you want, mark it as an artifact window
with the **+** button above the chart and drag over it; those stretches are then
kept out of the filter estimation instead of corrupting it.

Open the sidebar with the hamburger button on the left to fill in what this
recording is: the project it belongs to, the subject, session and task labels,
and the muscle behind each grid. This is worth a minute of your time, because it
is what makes your output findable and reusable later. The same sidebar holds
the decomposition settings — number of iterations, analysis windows, duplicate
threshold, peel-off, and the quality filters applied afterwards. The defaults
are sensible starting points, so leave them alone until you have a reason not
to.

### Decomposing

Press **Decompose Signal** and watch the progress bar. When it finishes, MUedit
loads the result straight into the editor — there is no file to go and open.

### Editing the motor units

This is where most of your time goes.
Decomposition is never perfect: it misses discharges, invents a few, and
occasionally splits one unit into two or merges two into one. The editor puts
each motor unit in front of you so you can fix that.

Step through the units with the **Grid** and **Motor Unit** dropdowns, or with
`<` and `>`. For each one you get its firing rate over time on top and its pulse
train underneath. Move around the pulse train with the arrow keys — left and
right to scroll, up and down to zoom — and double-click to jump back out to the
full recording.

To correct a unit, you pick a tool and then drag a box over the part of the
pulse train you want it to act on. `A` adds the missed discharges inside the
box, `D` deletes what should not be there, and `X` marks a peak as an artifact so
it is excluded rather than counted. `R` clears out spikes with implausibly high
discharge rates in one go. Pressing `Space` recomputes the unit's filter from
the original EMG over the window you are looking at, which is the single most
useful thing you can do to a marginal unit: it often turns a noisy, half-found
unit into a clean one. Two toggles change how that recomputation behaves —
peel-off (`P`) removes the other units' contributions first, and spike-locking
(`L`) keeps the edits you have already made instead of replacing them.

If a unit is beyond saving, flag it and it will be dropped when you save. If one
unit clearly contains two, duplicate it and edit the two copies apart. **Undo**
steps back through your edits on the current unit and **Reset** returns it to how
the decomposition left it, so nothing you try here is irreversible.

After each edit MUedit leaves a bookmark where you were working; zoom out and it
appears, so you can find your way back.

### Saving

Click **Save**. MUedit writes your corrected decomposition as a BIDS derivative
under your project folder, alongside a sidecar recording the full history of
every edit you made, with timestamps. That history survives save-and-reload
cycles, so you can come back to a file tomorrow and keep going, and anyone
looking at the data later can see exactly what was done by hand.

Each save also writes the discharges as a standard BIDS events table, one row
per spike, regenerated from your current edits.

By default everything lands under `data/` inside the MUedit folder, in a
subfolder named after the **Project** field you filled in. Type `study1` there
and your results appear in `data/study1/`; leave it blank and they go to
`data/muedit_out/`. You never type a full path. If you would rather keep your
data somewhere else entirely, set the `MUEDIT_DATA_ROOT` environment variable
before launching.

One thing to do before you share a dataset: a handful of dataset-level fields
(authors, licence, funding, ethics, task descriptions) cannot be typed into the
app. Run `notebooks/bids_dataset_metadata.ipynb` once per dataset to fill them
in and validate the result.

## When something goes wrong

If the browser never opens, go to <http://localhost:8080> manually. If the
launcher complains that a port is already in use, something else on your machine
has taken it — set `MUEDIT_BACKEND_PORT` or `MUEDIT_FRONTEND_PORT` to free
numbers and relaunch.

If the app tells you the backend is unreachable, the Python process behind the
interface has stopped. Close the terminal and run the launcher again; your saved
work is on disk and unaffected. A message about an expired session during a long
run is milder: MUedit lost its cached copy of the signal and is reloading it from
the original file. That normally recovers by itself, unless the file has moved
since you opened it.

If a file will not load at all, check the extension is one of those listed above,
and for BIDS recordings check that the `_channels.tsv` sidecar really is sitting
next to the `.bdf` or `.edf`. If saving or a filter update fails, it is almost
always the **Project** field left empty, or the original EMG file no longer being
where it was when you opened it.

## Learning more

The [user guide](docs/user-guide.md) covers every control, setting, and keyboard
shortcut in detail — read it when you want to know exactly what an option does.
[Saved files](docs/saved-files.md) documents everything MUedit writes to disk and
the contents of each output file, which is what you want when you start
analysing the results in your own scripts.

MUedit also has two command-line entry points, for batch work and scripting:
`muedit-api` starts just the backend, and `muedit-decompose` runs a
decomposition without the interface.

## For developers

Install the development tooling and enable the commit hooks:

```bash
uv sync --extra dev
uv run pre-commit install
```

`uv run` executes inside the project environment, so there is nothing to
activate:

```bash
uv run pytest                # full suite (needs recordings under data/)
uv run pytest -m "not data"  # the tier CI runs, no recordings required
uv run mypy
uv run ruff check . && uv run ruff format .
```

Two further extras are available: `--extra notebook` for JupyterLab and
`--extra research` for Optuna. They combine, e.g.
`uv sync --extra dev --extra notebook`.

`uv.lock` pins the whole transitive dependency graph and is committed, so every
machine and CI resolve identical versions. After editing dependencies in
`pyproject.toml`, run `uv lock` and commit the result — CI runs with `--locked`
and fails if the two have drifted. To move a single pin deliberately, use
`uv lock --upgrade-package numpy`.

The frontend has its own lint, format, type and test checks, which CI also
runs (Node 20 or later):

```bash
cd frontend
npm ci
npm run check   # eslint, prettier --check, tsc --checkJs, node --test
```

The architecture of the frontend and backend is documented under
[dev/app/](dev/app/) and [dev/backend/](dev/backend/).

> **Migrating from conda:** `environment.yml` is deprecated and kept for one
> release so in-flight work is not disrupted. It is no longer tested in CI and
> will be removed; `uv sync` reproduces the same package versions it pinned.
> `deno`, used only by the BIDS validation cell in
> `notebooks/bids_dataset_metadata.ipynb`, is not a Python package and is now
> installed separately (that notebook explains how).

## Acknowledgment

This project includes code from `adapt_decomp` (see
`python/src/muedit/adapt_decomp`) for adaptive decomposition workflows.
Original author: Irene Mendez Guerra;
original repository: <https://github.com/imendezguerra/adapt_decomp>.
