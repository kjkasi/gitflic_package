"""Idempotent import orchestration for scanned package artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .archive import ScanResult
from .client import DuplicateError, GitFlicClient


@dataclass(frozen=True)
class ArtifactResult:
    source_path: Path
    name: str
    version: str
    status: Literal[
        "uploaded", "skipped", "planned", "invalid", "source-duplicate", "failed"
    ]
    attempts: int
    message: str


@dataclass(frozen=True)
class ImportSummary:
    results: tuple[ArtifactResult, ...]
    inventory_requests: int
    uploaded: int
    skipped: int
    planned: int
    invalid: int
    source_duplicates: int
    failed: int
    ok: bool


class InventoryError(RuntimeError):
    """Raised when destination inventory cannot be safely loaded."""


def _request_attempts(client: GitFlicClient) -> int:
    attempts = getattr(client, "last_request_attempts", 1)
    return attempts if isinstance(attempts, int) and attempts >= 0 else 1


def run_import(
    scan: ScanResult, client: GitFlicClient, dry_run: bool = False
) -> ImportSummary:
    try:
        existing = client.list_package_versions()
        inventory_requests = getattr(client, "last_inventory_requests", 1)
        if not isinstance(inventory_requests, int) or inventory_requests < 1:
            inventory_requests = 1
    except Exception as error:
        raise InventoryError(f"could not load GitFlic package inventory: {error}") from error

    results: list[ArtifactResult] = []
    for artifact in sorted(scan.artifacts, key=lambda item: item.source_path):
        key = (artifact.full_name, artifact.version)
        if key in existing:
            results.append(
                ArtifactResult(
                    source_path=artifact.source_path,
                    name=artifact.full_name,
                    version=artifact.version,
                    status="skipped",
                    attempts=0,
                    message="version already exists",
                )
            )
            continue
        if dry_run:
            results.append(
                ArtifactResult(
                    source_path=artifact.source_path,
                    name=artifact.full_name,
                    version=artifact.version,
                    status="planned",
                    attempts=0,
                    message="upload planned",
                )
            )
            continue
        try:
            client.upload(artifact)
        except DuplicateError as error:
            results.append(
                ArtifactResult(
                    source_path=artifact.source_path,
                    name=artifact.full_name,
                    version=artifact.version,
                    status="skipped",
                    attempts=_request_attempts(client),
                    message=str(error),
                )
            )
        except Exception as error:
            results.append(
                ArtifactResult(
                    source_path=artifact.source_path,
                    name=artifact.full_name,
                    version=artifact.version,
                    status="failed",
                    attempts=_request_attempts(client),
                    message=str(error),
                )
            )
        else:
            results.append(
                ArtifactResult(
                    source_path=artifact.source_path,
                    name=artifact.full_name,
                    version=artifact.version,
                    status="uploaded",
                    attempts=_request_attempts(client),
                    message="uploaded",
                )
            )

    for issue in sorted(scan.issues, key=lambda item: item.source_path):
        results.append(
            ArtifactResult(
                source_path=issue.source_path,
                name=issue.source_path.name,
                version="",
                status="invalid",
                attempts=0,
                message=issue.reason,
            )
        )
    for issue in sorted(scan.source_duplicates, key=lambda item: item.source_path):
        results.append(
            ArtifactResult(
                source_path=issue.source_path,
                name=issue.source_path.name,
                version="",
                status="source-duplicate",
                attempts=0,
                message=issue.reason,
            )
        )
    results.sort(key=lambda result: result.source_path)

    counts = {status: sum(result.status == status for result in results) for status in (
        "uploaded", "skipped", "planned", "invalid", "source-duplicate", "failed"
    )}
    return ImportSummary(
        results=tuple(results),
        inventory_requests=inventory_requests,
        uploaded=counts["uploaded"],
        skipped=counts["skipped"],
        planned=counts["planned"],
        invalid=counts["invalid"],
        source_duplicates=counts["source-duplicate"],
        failed=counts["failed"],
        ok=counts["invalid"] == 0 and counts["failed"] == 0,
    )
