"""Safe discovery and inspection of Verdaccio npm tarballs."""

from __future__ import annotations

import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PackageArtifact:
    source_path: Path
    filename: str
    full_name: str
    package_name: str
    package_scope: str | None
    version: str


@dataclass(frozen=True)
class ArchiveIssue:
    source_path: Path
    reason: str


@dataclass(frozen=True)
class ScanResult:
    artifacts: tuple[PackageArtifact, ...]
    issues: tuple[ArchiveIssue, ...]
    source_duplicates: tuple[ArchiveIssue, ...]


class ArchiveValidationError(ValueError):
    """Raised when an archive cannot provide valid npm package metadata."""


def discover_archives(source_dir: Path) -> tuple[Path, ...]:
    """Return recursively discovered tarballs in deterministic path order."""
    return tuple(
        sorted(
            path
            for path in Path(source_dir).rglob("*")
            if path.is_file() and path.name.lower().endswith(".tgz")
        )
    )


def _metadata_name(value: object) -> tuple[str, str, str | None]:
    if not isinstance(value, str) or not value:
        raise ArchiveValidationError("package name must be a non-empty string")
    if value.startswith("@"):
        match = re.fullmatch(r"@([^/]+)/([^/]+)", value)
        if not match:
            raise ArchiveValidationError("package name has invalid scoped form")
        scope, package_name = match.groups()
        return value, package_name, scope
    if "/" in value or "\\" in value or value.startswith("@"):
        raise ArchiveValidationError("package name has invalid unscoped form")
    return value, value, None


def _metadata_version(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ArchiveValidationError("package version must be a non-empty string")
    if any(character in value for character in "/\\\x00\r\n"):
        raise ArchiveValidationError("package version is not safe for a URL path")
    return value


def inspect_archive(path: Path) -> PackageArtifact:
    """Inspect package/package.json without extracting the archive."""
    path = Path(path)
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = [member for member in archive.getmembers() if member.name == "package/package.json"]
            if not members:
                raise ArchiveValidationError("missing package/package.json")
            member = members[0]
            if member.issym() or member.islnk():
                raise ArchiveValidationError("package/package.json must not be a symlink")
            if not member.isreg():
                raise ArchiveValidationError("package/package.json must be a regular file")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ArchiveValidationError("could not read package/package.json")
            try:
                metadata = json.load(extracted)
            except (OSError, json.JSONDecodeError) as error:
                raise ArchiveValidationError(f"invalid package/package.json: {error}") from error
    except ArchiveValidationError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise ArchiveValidationError(f"invalid tarball: {error}") from error

    if not isinstance(metadata, dict):
        raise ArchiveValidationError("package/package.json must contain an object")
    full_name, package_name, package_scope = _metadata_name(metadata.get("name"))
    version = _metadata_version(metadata.get("version"))
    return PackageArtifact(
        source_path=path,
        filename=path.name,
        full_name=full_name,
        package_name=package_name,
        package_scope=package_scope,
        version=version,
    )


def scan_archives(source_dir: Path) -> ScanResult:
    """Inspect all discovered archives, retaining issues rather than aborting."""
    artifacts: list[PackageArtifact] = []
    issues: list[ArchiveIssue] = []
    source_duplicates: list[ArchiveIssue] = []
    seen: set[tuple[str, str]] = set()

    for path in discover_archives(source_dir):
        try:
            artifact = inspect_archive(path)
        except ArchiveValidationError as error:
            issues.append(ArchiveIssue(source_path=path, reason=str(error)))
            continue
        key = (artifact.full_name, artifact.version)
        if key in seen:
            source_duplicates.append(
                ArchiveIssue(source_path=path, reason="duplicate source package version")
            )
            continue
        seen.add(key)
        artifacts.append(artifact)

    return ScanResult(
        artifacts=tuple(artifacts),
        issues=tuple(issues),
        source_duplicates=tuple(source_duplicates),
    )
