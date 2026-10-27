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

MUedit is a research tool, actively used and actively maintained. We keep
extending it as our own projects need it. The adaptive decomposition option is
still being tuned, so treat it as experimental. If something is awkward or
missing, we would rather hear about it than not.

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
`MUedit.bat` on Windows. A terminal window appears and reports what it is doing,
then MUedit opens in your browser, at <http://127.0.0.1:8000>. The app runs in
that terminal window. When you are finished, quit it there with `Ctrl+C`, or by
closing the window. Closing the browser tab on its own does **not** quit MUedit,
and nothing is lost if you close it by accident: reopen <http://127.0.0.1:8000>
and carry on where you were.

Two things to know the first time. On macOS, if you downloaded MUedit rather
than cloning it, macOS will refuse to open the launcher from an unidentified
developer. Right-click **MUedit.command**, choose **Open**, and confirm once.
Every double-click after that works normally. On Windows, nothing special is
needed. The `.bat` launcher already tells PowerShell to allow this one script,
so you do not have to change any system-wide setting.

If you prefer a terminal, or you are on Linux, the same launchers live in
`scripts/` and can be run directly:

```bash
./scripts/run_MUedit.sh          # macOS / Linux
.\scripts\run_MUedit.ps1         # Windows PowerShell
```

## A first session

A session moves through four stages, shown as buttons across the top of the window. They
unlock in order as you go:

1. **Import** — click the folder icon on the landing page and pick your recording.
2. **QC / ROI** — discard noisy channels (**Automatic QC** finds most of them), drag across
   the average EMG chart to choose the window to decompose, and fill in the session details
   in the settings panel on the left. A decomposition will not start until they are filled in.
3. **Decompose** — press **Decompose signal** and watch the search. When it finishes, the
   result is saved and already loaded into the editor.
4. **Edit** — step through the motor units, add missed discharges, delete false ones,
   recompute a unit's filter over the window you are looking at (`Space`), and **Save**.
   Your edits are journaled as you go, so a crash does not lose them.

MUedit reads the common high-density EMG formats directly, so in most cases there is no
conversion step:

| Recording from | File to open |
|---|---|
| MATLAB | `.mat` (both v5 and v7.3) |
| OT Biolab+ | `.otb+` |
| OT Biolab 4 | `.otb4` |
| Intan | `.rhd` file, or the recording folder |
| Open Ephys (binary format) | `structure.oebin`, or the recording folder |
| BIDS EMG | `.bdf` or `.edf`, with its `_channels.tsv` sidecar beside it |
| A previous MUedit session | `.npz` — opens straight into the editor |

The [user guide](docs/user-guide.md) covers every control, setting and keyboard shortcut.

## Where your results go

Results land in a BIDS dataset named after the **Project** field, inside `data/` in the
MUedit folder: pick **+ New project…**, type `study1`, and your results appear in
`data/study1/`. The file to analyse is the `_edited.npz` under `derivatives/muedit/`.
[Saved files](docs/saved-files.md) documents every file MUedit writes and how to read them in
your own scripts, and how to keep your data somewhere else.

Before you share a dataset, run `notebooks/bids_dataset_metadata.ipynb` once to fill in the
dataset-level fields the app cannot ask for (authors, licence, funding, ethics) and validate
the result.

## When something goes wrong

If the browser never opens, go to <http://127.0.0.1:8000> manually. When something fails, the
details are in MUedit's log file:

| OS | Log file |
|---|---|
| macOS | `~/Library/Logs/MUedit/muedit.log` |
| Windows | `%LOCALAPPDATA%\MUedit\Logs\muedit.log` |
| Linux | `~/.local/state/muedit/log/muedit.log` |

[Troubleshooting](docs/troubleshooting.md) lists common symptoms and their fixes: a port
already in use, an unreachable backend, a file that will not load, a failed filter update.
If something is awkward or missing, please
[open an issue](https://github.com/simonavrillon/MUedit2/issues) and include the log file.

## Command line

`muedit api` starts just the backend, and `muedit decompose` runs a decomposition without the
interface, for batch work and scripting. See the [command-line reference](docs/cli.md) for
every flag and the environment variables.

## For developers

```bash
uv sync --extra dev
uv run pre-commit install
```

Then start at [AGENTS.md](AGENTS.md): the documentation map, the commands to test and lint,
the rules every change follows, and links to the architecture, contracts and change recipes
in [dev/](dev/). Optional extras: `--extra notebook` (JupyterLab), `--extra research`
(Optuna), `--extra plot` (matplotlib, for the CLI's `--manual-roi`).

## License

MUedit is free software, released under the [GNU General Public License v3.0](LICENSE).

## Acknowledgment

This project includes code from `adapt_decomp` (see
`python/src/muedit/decomp/adaptation.py`) for adaptive decomposition workflows.
Original author: Irene Mendez Guerra;
original repository: <https://github.com/imendezguerra/adapt_decomp>.
