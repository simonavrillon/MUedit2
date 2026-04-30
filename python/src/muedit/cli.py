"""Canonical CLI entrypoints for MUedit web/API and decomposition tasks."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import uvicorn

from muedit.api.app_factory import create_app
from muedit.api.routes import include_routers
from muedit.decomp.pipeline import DecompositionParameters, run_decomposition


def _parse_roi(value: str) -> tuple[int, int]:
    """Parse a single ROI argument in ``start,end`` sample format."""
    parts = value.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("ROI must be 'start,end' in samples.")
    try:
        start = int(parts[0].strip())
        end = int(parts[1].strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ROI values must be integers.") from exc
    return start, end


def _parse_rois(value: str) -> list[tuple[int, int]]:
    """Parse multiple ROI ranges separated by ``;``."""
    rois: list[tuple[int, int]] = []
    for part in value.split(";"):
        if not part.strip():
            continue
        rois.append(_parse_roi(part))
    if not rois:
        raise argparse.ArgumentTypeError("ROIs must include at least one 'start,end'.")
    return rois


def serve_api() -> None:
    """Start the FastAPI backend server."""
    app = create_app(title="MUedit API", version="2.0.0")
    include_routers(app)
    host = os.environ.get("MUEDIT_HOST", "0.0.0.0")
    port = int(os.environ.get("MUEDIT_PORT") or os.environ.get("MUEDIT_BACKEND_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


def run_decomposition_cli() -> None:
    """Run end-to-end decomposition from command-line arguments."""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    parser = argparse.ArgumentParser(description="Run MUedit decomposition.")
    parser.add_argument(
        "filepath",
        nargs="?",
        help="Input file path (.mat, .otb+, .otb4). If omitted, uses sample data.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Analyze only the first N seconds.",
    )
    parser.add_argument(
        "--manual-roi",
        action="store_true",
        help="Select ROI interactively with a plot.",
    )
    parser.add_argument(
        "--roi",
        type=_parse_roi,
        default=None,
        help="Single ROI in samples: 'start,end'.",
    )
    parser.add_argument(
        "--rois",
        type=_parse_rois,
        default=None,
        help="Multiple ROIs in samples: 'start,end;start,end'.",
    )
    parser.add_argument(
        "--niter",
        type=int,
        default=150,
        help="Iterations (app setting: Iterations).",
    )
    parser.add_argument(
        "--nwindows",
        type=int,
        default=1,
        help="Analysis windows count (app setting: Analysis windows).",
    )
    parser.add_argument(
        "--duplicatesthresh",
        type=float,
        default=0.3,
        help="Duplicate-removal threshold (app setting: Duplicates thresh).",
    )
    parser.add_argument(
        "--sil-thr",
        type=float,
        default=0.9,
        help="SIL threshold (app setting: SIL threshold).",
    )
    parser.add_argument(
        "--sil-filter",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable SIL filter (app setting: SIL filter).",
    )
    parser.add_argument(
        "--cov-thr",
        type=float,
        default=0.5,
        help="COV threshold (app setting: COV threshold).",
    )
    parser.add_argument(
        "--cov-filter",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable/disable COV filter (app setting: COV tool).",
    )
    parser.add_argument(
        "--peel-off",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable/disable peel-off (app setting: Peeloff).",
    )
    parser.add_argument(
        "--peel-off-window-ms",
        type=float,
        default=25.0,
        help="Peel-off window in milliseconds (app setting: Window (ms)).",
    )
    parser.add_argument(
        "--use-adaptive",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable/disable adaptive mode (app setting: Use adaptive).",
    )
    parser.add_argument(
        "--bids-root",
        type=str,
        default=None,
        help="Export preprocessed raw EMG to BIDS at this root path.",
    )
    parser.add_argument("--subject", type=str, default="01", help="BIDS subject label.")
    parser.add_argument("--task", type=str, default="task", help="BIDS task label.")
    parser.add_argument("--session", type=str, default=None, help="BIDS session label.")
    parser.add_argument("--run", type=str, default=None, help="BIDS run label.")
    parser.add_argument(
        "--bids-metadata",
        type=str,
        default=None,
        help="Additional JSON metadata merged into *_emg.json.",
    )
    parser.add_argument(
        "--bids-metadata-file",
        type=str,
        default=None,
        help="Path to JSON object merged into *_emg.json.",
    )
    args = parser.parse_args()

    if args.roi and args.rois:
        parser.error("Use either --roi or --rois, not both.")
    if args.bids_metadata and args.bids_metadata_file:
        parser.error("Use either --bids-metadata or --bids-metadata-file, not both.")
    if (args.bids_metadata or args.bids_metadata_file) and not args.bids_root:
        parser.error("--bids-metadata* requires --bids-root.")
    if args.niter < 1:
        parser.error("--niter must be >= 1.")
    if args.nwindows < 1:
        parser.error("--nwindows must be >= 1.")
    if args.peel_off_window_ms <= 0:
        parser.error("--peel-off-window-ms must be > 0.")

    if args.filepath:
        full_path = Path(args.filepath)
        if not full_path.exists():
            parser.error(f"File not found: {full_path}")
        file_label = full_path.name
    else:
        sample_dir = Path(__file__).resolve().parents[3] / "data" / "datasamples"
        if not sample_dir.exists():
            parser.error(
                f"No input filepath provided and sample directory is missing: {sample_dir}"
            )
        sample_candidates = sorted(
            [
                p
                for p in sample_dir.iterdir()
                if p.is_file() and p.suffix.lower() in {".mat", ".otb4", ".otb+"}
            ]
        )
        if not sample_candidates:
            parser.error(
                "No input filepath provided and no sample file found in data/datasamples."
            )
        full_path = sample_candidates[0]
        file_label = full_path.name

    bids_entities = None
    bids_metadata = None
    if args.bids_root:
        bids_entities = {
            "subject": args.subject,
            "task": args.task,
            "session": args.session,
            "run": args.run,
        }
        if args.bids_metadata:
            try:
                parsed_meta = json.loads(args.bids_metadata)
            except json.JSONDecodeError as exc:
                parser.error(f"--bids-metadata must be valid JSON: {exc}")
            if not isinstance(parsed_meta, dict):
                parser.error("--bids-metadata must decode to a JSON object")
            bids_metadata = parsed_meta
        elif args.bids_metadata_file:
            meta_path = Path(args.bids_metadata_file)
            if not meta_path.exists():
                parser.error(f"--bids-metadata-file not found: {meta_path}")
            try:
                parsed_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                parser.error(f"--bids-metadata-file must contain valid JSON: {exc}")
            except OSError as exc:
                parser.error(f"Could not read --bids-metadata-file: {exc}")
            if not isinstance(parsed_meta, dict):
                parser.error("--bids-metadata-file must decode to a JSON object")
            bids_metadata = parsed_meta

    params = DecompositionParameters(
        niter=args.niter,
        nwindows=args.nwindows,
        nbextchan=1000,
        duplicatesthresh=args.duplicatesthresh,
        sil_thr=(float("-inf") if not args.sil_filter else args.sil_thr),
        cov_thr=args.cov_thr,
        covfilter=1 if args.cov_filter else 0,
        contrast_func="skew",
        initialization=0,
        peel_off_enabled=1 if args.peel_off else 0,
        peel_off_win=args.peel_off_window_ms / 1000.0,
        use_adaptive=args.use_adaptive,
    )

    run_decomposition(
        str(full_path),
        duration=args.duration,
        manual_roi=args.manual_roi,
        roi=args.roi,
        rois=args.rois,
        params=params,
        bids_root=args.bids_root,
        bids_entities=bids_entities,
        bids_metadata=bids_metadata,
        file_label=file_label,
    )


def main() -> None:
    """Dispatch the top-level ``muedit`` CLI subcommands."""
    parser = argparse.ArgumentParser(prog="muedit")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api", help="Run FastAPI backend")
    sub.add_parser("decompose", help="Run decomposition CLI")
    args, _unknown = parser.parse_known_args()

    if args.command == "api":
        serve_api()
    elif args.command == "decompose":
        run_decomposition_cli()


if __name__ == "__main__":
    main()
