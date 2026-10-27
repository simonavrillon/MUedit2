"""Canonical CLI entrypoints for MUedit web/API and decomposition tasks."""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import sys
from pathlib import Path
from typing import get_args

import uvicorn

from muedit.api.app_factory import create_app, mount_frontend
from muedit.api.routes import include_routers
from muedit.app_log import log_to_file
from muedit.decomp.pipeline import run_decomposition
from muedit.decomp.types import (
    DEFAULT_POSTPROCESS_MODE,
    POSTPROCESS_MODES,
    ComputeDtype,
    ContrastFunc,
    DecompositionParameters,
)
from muedit.errors import MueditError
from muedit.io.factory import supported_extensions
from muedit.paths import frontend_dir, log_dir

_DEFAULT_PARAMS = DecompositionParameters()


def _parse_rois(value: str) -> list[tuple[int, int]]:
    """Parse ``start,end`` sample ranges separated by ``;``."""
    rois: list[tuple[int, int]] = []
    for part in value.split(";"):
        if not part.strip():
            continue
        bounds = part.split(",")
        if len(bounds) != 2:
            raise argparse.ArgumentTypeError("ROI must be 'start,end' in samples.")
        try:
            rois.append((int(bounds[0].strip()), int(bounds[1].strip())))
        except ValueError as exc:
            raise argparse.ArgumentTypeError("ROI values must be integers.") from exc
    if not rois:
        raise argparse.ArgumentTypeError("ROIs must include at least one 'start,end'.")
    return rois


LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def serve_api() -> None:
    """Start the MUedit server: the API, and the frontend at ``/`` on the same origin."""
    # Decompositions run in spawned worker processes; a frozen (bundled) app must let them start.
    multiprocessing.freeze_support()
    log_to_file(log_dir() / "muedit.log")
    # /preview-by-path reads any local path, so only this machine's frontend may call it.
    host = os.environ.get("MUEDIT_HOST", "127.0.0.1")
    port = int(os.environ.get("MUEDIT_PORT", "8000"))
    app = create_app(allowed_hosts=LOOPBACK_HOSTS if host in LOOPBACK_HOSTS else None)
    include_routers(app)
    frontend = frontend_dir()
    if frontend is not None:
        mount_frontend(app, frontend)
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


def run_decomposition_cli(argv: list[str] | None = None) -> None:
    """Run end-to-end decomposition from command-line arguments."""
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    parser = argparse.ArgumentParser(
        prog="muedit decompose", description="Run MUedit decomposition."
    )
    parser.add_argument(
        "file_path",
        help=(
            f"Recording file ({', '.join(supported_extensions())}) or a BIDS EMG, Intan or "
            "Open Ephys recording folder."
        ),
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
        "--rois",
        type=_parse_rois,
        default=None,
        help="ROIs in samples: 'start,end' or 'start,end;start,end'.",
    )
    parser.add_argument(
        "--niter",
        type=int,
        default=_DEFAULT_PARAMS.niter,
        help="Iterations (app setting: Iterations).",
    )
    parser.add_argument(
        "--nwindows",
        type=int,
        default=_DEFAULT_PARAMS.nwindows,
        help="Analysis windows count (app setting: Analysis windows).",
    )
    parser.add_argument(
        "--duplicatesthresh",
        type=float,
        default=_DEFAULT_PARAMS.duplicatesthresh,
        help="Duplicate-removal threshold (app setting: Duplicates thresh).",
    )
    parser.add_argument(
        "--sil-thr",
        type=float,
        default=_DEFAULT_PARAMS.sil_thr,
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
        default=_DEFAULT_PARAMS.cov_thr,
        help="COV threshold (app setting: COV threshold).",
    )
    parser.add_argument(
        "--covfilter",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULT_PARAMS.covfilter,
        help="Enable/disable COV filter (app setting: COV tool).",
    )
    parser.add_argument(
        "--contrast-func",
        type=str,
        choices=get_args(ContrastFunc),
        default=_DEFAULT_PARAMS.contrast_func,
        help="FastICA contrast function (app setting: Contrast func).",
    )
    parser.add_argument(
        "--compute-dtype",
        choices=get_args(ComputeDtype),
        default=_DEFAULT_PARAMS.compute_dtype,
        help="Type of the filtered EMG and the extended/whitened windows (float32 halves their memory).",
    )
    parser.add_argument(
        "--random-init",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULT_PARAMS.random_init,
        help="Seed each ICA filter randomly instead of from the most active sample.",
    )
    parser.add_argument(
        "--peel-off-enabled",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULT_PARAMS.peel_off_enabled,
        help="Enable/disable peel-off (app setting: Peeloff).",
    )
    parser.add_argument(
        "--peel-off-win",
        type=float,
        default=_DEFAULT_PARAMS.peel_off_win,
        help="Peel-off window in seconds (app setting: Window (ms), divided by 1000).",
    )
    parser.add_argument(
        "--postprocess",
        choices=sorted(POSTPROCESS_MODES),
        default=DEFAULT_POSTPROCESS_MODE,
        help="Post-processing route (app setting: Post-processing).",
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
        "--auto-mask-artifacts",
        action=argparse.BooleanOptionalAction,
        default=_DEFAULT_PARAMS.auto_mask_artifacts,
        help=("Enable/disable automatic QC pipeline (bad-channel detection and artifact masking)."),
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

    postprocess_flags = POSTPROCESS_MODES[args.postprocess]
    if args.bids_metadata and args.bids_metadata_file:
        parser.error("Use either --bids-metadata or --bids-metadata-file, not both.")
    if (args.bids_metadata or args.bids_metadata_file) and not args.bids_root:
        parser.error("--bids-metadata* requires --bids-root.")

    try:
        params = DecompositionParameters(
            niter=args.niter,
            nwindows=args.nwindows,
            duplicatesthresh=args.duplicatesthresh,
            sil_thr=(float("-inf") if not args.sil_filter else args.sil_thr),
            cov_thr=args.cov_thr,
            covfilter=args.covfilter,
            contrast_func=args.contrast_func,
            random_init=args.random_init,
            peel_off_enabled=args.peel_off_enabled,
            peel_off_win=args.peel_off_win,
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
            auto_mask_artifacts=args.auto_mask_artifacts,
            compute_dtype=args.compute_dtype,
        )
    except ValueError as exc:
        parser.error(str(exc))

    full_path = Path(args.file_path)
    if not full_path.exists():
        parser.error(f"File not found: {full_path}")

    bids_entities = None
    bids_metadata = None
    if args.bids_root:
        bids_entities = {
            "subject": args.subject,
            "task": args.task,
            "session": args.session,
            "run": args.run,
        }
        source, text = "--bids-metadata", args.bids_metadata
        if args.bids_metadata_file:
            source = "--bids-metadata-file"
            try:
                text = Path(args.bids_metadata_file).read_text(encoding="utf-8")
            except OSError as exc:
                parser.error(f"Could not read {source}: {exc}")
        if text is not None:
            try:
                bids_metadata = json.loads(text)
            except json.JSONDecodeError as exc:
                parser.error(f"{source} must be valid JSON: {exc}")
            if not isinstance(bids_metadata, dict):
                parser.error(f"{source} must decode to a JSON object")

    try:
        run_decomposition(
            str(full_path),
            duration=args.duration,
            manual_roi=args.manual_roi,
            rois=args.rois,
            params=params,
            bids_root=args.bids_root,
            bids_entities=bids_entities,
            bids_metadata=bids_metadata,
            file_label=full_path.name,
        )
    except MueditError as exc:
        parser.exit(1, f"muedit: {exc}\n")


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
