"""Command-line interface for GitFlic npm package migration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .archive import scan_archives
from .client import GitFlicClient, GitFlicError, UrlLibTransport
from .config import load_config, resolve_token
from .importer import InventoryError, run_import
from .limiter import RateLimiter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitflic-package",
        description="Migrate npm tarballs into a GitFlic project registry.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("validate", "scan and validate local npm tarballs"),
        ("import", "plan or import npm tarballs into GitFlic"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--config", required=True, type=Path)
        command.add_argument("--verbose", action="store_true")
        if name == "import":
            command.add_argument("--dry-run", action="store_true")
    return parser


def _print_scan(scan, verbose: bool) -> None:
    for issue in scan.issues:
        print(f"invalid: {issue.source_path}: {issue.reason}")
    for issue in scan.source_duplicates:
        print(f"source-duplicate: {issue.source_path}: {issue.reason}")
    if verbose:
        for artifact in scan.artifacts:
            print(f"valid: {artifact.source_path}: {artifact.full_name}@{artifact.version}")
    print(
        "Validation summary: "
        f"valid={len(scan.artifacts)} invalid={len(scan.issues)} "
        f"source-duplicates={len(scan.source_duplicates)}"
    )


def _print_import_summary(summary, verbose: bool) -> None:
    for result in summary.results:
        detail = f": {result.message}"
        if verbose:
            detail += f" (source={result.source_path}, attempts={result.attempts})"
        print(f"{result.status}: {result.name} {result.version}{detail}")
    print(
        "Import summary: "
        f"uploaded={summary.uploaded} skipped={summary.skipped} "
        f"planned={summary.planned} invalid={summary.invalid} "
        f"source-duplicates={summary.source_duplicates} failed={summary.failed}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    token: str | None = None
    try:
        config = load_config(args.config)
        scan = scan_archives(config.source_dir)
        if args.command == "validate":
            _print_scan(scan, args.verbose)
            return 0 if not scan.issues else 1

        token = resolve_token(config.gitflic)
        limiter = RateLimiter(
            config.rate_limit.min_interval_seconds,
            max_retries=config.rate_limit.max_retries,
        )
        client = GitFlicClient(
            config=config.gitflic,
            token=token,
            rate_limiter=limiter,
            transport=UrlLibTransport(),
        )
        summary = run_import(scan, client, dry_run=args.dry_run)
        _print_import_summary(summary, args.verbose)
        return 0 if summary.ok else 1
    except (ValueError, InventoryError, GitFlicError, OSError) as error:
        message = str(error)
        if token:
            message = message.replace(token, "[REDACTED]")
        print(f"error: {message}", file=sys.stderr)
        return 1
