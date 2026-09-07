"""Canonical CLI entrypoints for MUedit web/API and decomposition tasks."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import uvicorn

from muedit.api.app_factory import create_app
from muedit.api.routes import include_routers
from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.types import (
    DEFAULT_NBEXTCHAN,
    DEFAULT_PEEL_OFF_WIN_SEC,
    DEFAULT_POSTPROCESS_MODE,
    POSTPROCESS_MODES,
    DecompositionParameters,
)

_DEFAULT_PARAMS = DecompositionParameters()


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
    app = create_app(title="MUedit API", version="2.1.0")
    include_routers(app)
    host = os.environ.get("MUEDIT_HOST", "0.0.0.0")
    port = int(os.environ.get("MUEDIT_PORT") or os.environ.get("MUEDIT_BACKEND_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


def run_decomposition_cli(argv: list[str] | None = None) -> None:
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
        default=0.88,
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
        "--contrast-func",
        type=str,
        choices=["skew", "kurtosis", "logcosh"],
        default=_DEFAULT_PARAMS.contrast_func,
        help="FastICA contrast function (app setting: Contrast func).",
    )
    parser.add_argument(
        "--initialization",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULT_PARAMS.initialization,
        help="Enable/disable ICA initialization (app setting: Initialization).",
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
        default=DEFAULT_PEEL_OFF_WIN_SEC * 1000,
        help="Peel-off window in milliseconds (app setting: Window (ms)).",
    )
    parser.add_argument(
        "--postprocess",
        choices=sorted(POSTPROCESS_MODES),
        default=None,
        help=(
            "Post-processing route (app setting: Post-processing). "
            f"Default: {DEFAULT_POSTPROCESS_MODE}. Mutually exclusive with the "
            "legacy --use-adaptive/--full-trace flags."
        ),
    )
    parser.add_argument(
        "--use-adaptive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Deprecated alias for --postprocess adaptive; "
            "cannot be combined with --full-trace or --postprocess."
        ),
    )
    parser.add_argument(
        "--adapt-batch-ms",
        type=int,
        default=_DEFAULT_PARAMS.adapt_batch_ms,
        help="Adaptive batch duration in milliseconds.",
    )
    parser.add_argument(
        "--adapt-wh",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULT_PARAMS.adapt_wh,
        help="Enable/disable adaptive whitening matrix update.",
    )
    parser.add_argument(
        "--adapt-sv",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULT_PARAMS.adapt_sv,
        help="Enable/disable adaptive separation vector update.",
    )
    parser.add_argument(
        "--adapt-sd",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULT_PARAMS.adapt_sd,
        help="Enable/disable adaptive spike detection (centroid update).",
    )
    parser.add_argument(
        "--adapt-wh-learning-rate",
        type=float,
        default=_DEFAULT_PARAMS.adapt_wh_learning_rate,
        help="Learning rate for adaptive whitening matrix update.",
    )
    parser.add_argument(
        "--adapt-sv-learning-rate",
        type=float,
        default=_DEFAULT_PARAMS.adapt_sv_learning_rate,
        help="Learning rate for adaptive separation vector update.",
    )
    parser.add_argument(
        "--adapt-cov-alpha",
        type=float,
        default=_DEFAULT_PARAMS.adapt_cov_alpha,
        help="EMA weight for online covariance estimate.",
    )
    parser.add_argument(
        "--adapt-spike-prev-weight",
        type=int,
        default=_DEFAULT_PARAMS.adapt_spike_prev_weight,
        help="Inertia weight for centroid EMA update (higher = slower adaptation).",
    )
    parser.add_argument(
        "--full-trace",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Deprecated alias for --postprocess full-trace; "
            "cannot be combined with --use-adaptive or --postprocess."
        ),
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
    args = parser.parse_args(argv)

    if args.roi and args.rois:
        parser.error("Use either --roi or --rois, not both.")

    legacy_used = args.use_adaptive is not None or args.full_trace is not None
    if args.postprocess is not None and legacy_used:
        parser.error(
            "Use --postprocess or the legacy --use-adaptive/--full-trace flags, "
            "not both."
        )
    if args.use_adaptive and args.full_trace:
        parser.error(
            "--use-adaptive and --full-trace select different post-processing "
            "routes and cannot be combined; use --postprocess adaptive or "
            "--postprocess full-trace."
        )
    if args.postprocess is not None:
        mode = args.postprocess
    elif args.use_adaptive:
        mode = "adaptive"
    elif args.full_trace:
        mode = "full-trace"
    else:
        mode = DEFAULT_POSTPROCESS_MODE
    postprocess_flags = POSTPROCESS_MODES[mode]
    args.use_adaptive = postprocess_flags["use_adaptive"]
    args.full_trace = postprocess_flags["full_trace"]
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
    if args.adapt_batch_ms <= 0:
        parser.error("--adapt-batch-ms must be > 0.")
    if args.use_adaptive and args.adapt_batch_ms > _DEFAULT_PARAMS.edges_sec * 1000:
        parser.error(
            f"--adapt-batch-ms must be <= {_DEFAULT_PARAMS.edges_sec * 1000:.0f} "
            f"(edges_sec * 1000) when adaptive mode is enabled; larger batches "
            f"exceed the calibration window and produce a loss-tracking gap."
        )
    if args.adapt_wh_learning_rate <= 0:
        parser.error("--adapt-wh-learning-rate must be > 0.")
    if args.adapt_sv_learning_rate <= 0:
        parser.error("--adapt-sv-learning-rate must be > 0.")
    if not (0.0 < args.adapt_cov_alpha <= 1.0):
        parser.error("--adapt-cov-alpha must be in (0, 1].")
    if args.adapt_spike_prev_weight < 0:
        parser.error("--adapt-spike-prev-weight must be >= 0.")

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
            parser.error("No input filepath provided and no sample file found in data/datasamples.")
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
        nbextchan=DEFAULT_NBEXTCHAN,
        duplicatesthresh=args.duplicatesthresh,
        sil_thr=(float("-inf") if not args.sil_filter else args.sil_thr),
        cov_thr=args.cov_thr,
        covfilter=args.cov_filter,
        contrast_func=args.contrast_func,
        initialization=args.initialization,
        peel_off_enabled=args.peel_off,
        peel_off_win=args.peel_off_window_ms / 1000.0,
        use_adaptive=postprocess_flags["use_adaptive"],
        adapt_batch_ms=args.adapt_batch_ms,
        adapt_wh=args.adapt_wh,
        adapt_sv=args.adapt_sv,
        adapt_sd=args.adapt_sd,
        adapt_wh_learning_rate=args.adapt_wh_learning_rate,
        adapt_sv_learning_rate=args.adapt_sv_learning_rate,
        adapt_cov_alpha=args.adapt_cov_alpha,
        adapt_spike_prev_weight=args.adapt_spike_prev_weight,
        full_trace=postprocess_flags["full_trace"],
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
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        is_help = len(sys.argv) >= 2
        print("usage: muedit {api,decompose} ...", file=None if is_help else sys.stderr)
        sys.exit(0 if is_help else 2)
    command = sys.argv[1]
    if command == "api":
        serve_api()
    elif command == "decompose":
        run_decomposition_cli(argv=sys.argv[2:])
    else:
        print(f"muedit: unknown command '{command}'", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
