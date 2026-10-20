# Troubleshooting

MUedit runs entirely on your machine, so most problems are local: a port already in
use, a file that moved, or a missing sidecar. This page collects the common symptoms
and where to look. The [user guide](user-guide.md) covers normal use;
[saved-files.md](saved-files.md) covers output locations and file formats;
[cli.md](cli.md) covers the command line and environment variables.

## Symptom / fix

| Symptom | Fix |
|---|---|
| The browser never opens | Go to <http://127.0.0.1:8000> manually. |
| "Port already in use" at launch | MUedit needs port 8000. Close any earlier MUedit terminal window, or quit the other program holding the port. To use a different port, set `MUEDIT_BACKEND_PORT` when launching via `MUedit.command` / `MUedit.bat` / `scripts/run_MUedit.*` (see [cli.md](cli.md#environment-variables)). |
| "Backend unreachable — please restart the app" | The Python process behind the interface has stopped or crashed. Close the terminal and run the launcher again; your saved work is on disk and unaffected. |
| "Session expired, reloading file..." during a run | MUedit lost its cached copy of the signal and is reloading it from the original file, then retries once. This normally recovers by itself. If the file has moved since you opened it, reopen it. |
| A file will not load | Check the extension is one of `.mat`, `.otb+`, `.otb4`, `.bdf`, `.edf`, `.rhd`, `.oebin` (raw) or `.npz` / a MUedit `.mat` (a saved decomposition). For BIDS recordings, confirm the `*_channels.tsv` sidecar really is sitting next to the `.bdf` / `.edf`. |
| **Update filter** fails | Filter updates read the EMG from the BIDS dataset the decomposition belongs to (exported at save), or from the EMG embedded in a decomposition file saved outside a dataset. Make sure the **Project** field names that dataset and its `emg/` folder is intact. If the save reported a BIDS EMG error, the reopened file has no EMG to read. |
| **Save** fails | Check the **Project** field holds a single folder name, not a path — output is written to `<output>/<project>/` and a path such as `../study1` or `/data/x` is refused. |
| A decomposition opened from a BIDS dataset elsewhere saves back into that dataset | Expected while its **Project** field is unchanged; pick another project to save it elsewhere. See [where files go](saved-files.md#where-files-go). |
| **Automatic QC** overwrote my manual channel / artifact choices | It replaces them rather than merging: run it first, then adjust. See [Automatic QC](user-guide.md#automatic-qc). |
| Nothing happens when I press a keyboard shortcut in Edit | Shortcuts work only on the **Edit** stage, outside text fields, and not while the landing page is shown. See [navigation shortcuts](user-guide.md#navigation-shortcuts). |
| The **SIL filter** toggle will not turn off | It is always on in the app; only the command line can turn it off (`--no-sil-filter`, see [cli.md](cli.md#quality-filters-applied-after-decomposition)). |
| Two `.npz` files appeared after a run | Expected: reopen `<entity>_edited.npz`. See [what a run writes](saved-files.md#what-a-run-writes). |
| The **Decompose** step in the top bar is greyed out | It unlocks only once a run has started. Start a run with **Decompose signal** on the QC page. |
| Closing the tab asks "Leave site?" | You have unsaved edits. Stay and **Save**, or leave: the edits stay recoverable. See [unsaved edits](user-guide.md#unsaved-edits). |
| Reopening a file asks to restore unsaved edits | **OK** replays the edits an earlier session left unsaved; **Cancel** discards them. See [unsaved edits](user-guide.md#unsaved-edits). |

## Log file

When something fails, the details are in MUedit's log file. It rotates at 5 MB and
keeps 3 backups.

| OS | Log file |
|---|---|
| macOS | `~/Library/Logs/MUedit/muedit.log` |
| Windows | `%LOCALAPPDATA%\MUedit\Logs\muedit.log` |
| Linux | `~/.local/state/muedit/log/muedit.log` |

## Temporary files and cache

MUedit keeps two kinds of throwaway data in the per-user cache folder — the folder
`MUEDIT_CACHE_DIR` points at, or the OS default (`MUedit` on macOS/Windows, `muedit`
on Linux):

- **session stores** — memory-mapped copies of the signal you have open, so long
  recordings never sit whole on the heap;
- **unsaved-edit logs** (`edit-logs/`) — a journal of every edit, used to offer your
  unsaved work back when you reopen a file.

Idle sessions are closed automatically after 20 minutes, and edit logs are purged
after 30 days. It is safe to delete the whole cache folder when MUedit is closed: you
lose only cached signals and unsaved-edit recovery, never saved results. Set
`MUEDIT_CACHE_DIR` to move it somewhere else (see [cli.md](cli.md#environment-variables)).

## Where outputs go

See [where files go](saved-files.md#where-files-go).

## Reporting problems

MUedit is a research tool, actively used and actively maintained. If something is
awkward or missing, please report it — the authors would rather hear about it:
<https://github.com/simonavrillon/MUedit2/issues>. Include the log file (above) if you
can.
