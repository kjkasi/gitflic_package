import io
import json
import tarfile
from pathlib import Path

import pytest

from gitflic_package.archive import (
    ArchiveValidationError,
    discover_archives,
    inspect_archive,
    scan_archives,
)


def write_tgz(path: Path, metadata: object | None = None, *, symlink: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        if symlink:
            member = tarfile.TarInfo("package/package.json")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside.json"
            archive.addfile(member)
            return
        if metadata is None:
            metadata = {"name": "pkg", "version": "1.0.0"}
        payload = json.dumps(metadata).encode("utf-8")
        member = tarfile.TarInfo("package/package.json")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))


def test_discover_archives_is_recursive_case_insensitive_and_sorted(tmp_path: Path) -> None:
    paths = [tmp_path / "z.TGZ", tmp_path / "nested" / "a.tgz", tmp_path / "nope.zip"]
    for path in paths:
        if path.suffix.lower() == ".tgz":
            write_tgz(path)
        else:
            path.write_bytes(b"not an archive")

    assert discover_archives(tmp_path) == tuple(sorted((paths[0], paths[1])))


def test_inspect_archive_reads_unscoped_metadata(tmp_path: Path) -> None:
    path = tmp_path / "pkg-1.0.0.tgz"
    write_tgz(path, {"name": "pkg", "version": "1.0.0"})

    artifact = inspect_archive(path)

    assert artifact.source_path == path
    assert artifact.filename == path.name
    assert artifact.full_name == "pkg"
    assert artifact.package_name == "pkg"
    assert artifact.package_scope is None
    assert artifact.version == "1.0.0"


def test_inspect_archive_splits_scoped_name(tmp_path: Path) -> None:
    path = tmp_path / "scoped.tgz"
    write_tgz(path, {"name": "@scope/pkg", "version": "2.3.4"})

    artifact = inspect_archive(path)

    assert artifact.full_name == "@scope/pkg"
    assert artifact.package_scope == "scope"
    assert artifact.package_name == "pkg"


def test_scan_reports_missing_or_malformed_metadata(tmp_path: Path) -> None:
    missing = tmp_path / "missing.tgz"
    with tarfile.open(missing, "w:gz"):
        pass
    malformed = tmp_path / "malformed.tgz"
    malformed.write_bytes(b"not a tarball")
    valid = tmp_path / "valid.tgz"
    write_tgz(valid)

    result = scan_archives(tmp_path)

    assert [artifact.source_path for artifact in result.artifacts] == [valid]
    assert {issue.source_path for issue in result.issues} == {missing, malformed}
    assert all(issue.reason for issue in result.issues)


def test_scan_continues_after_invalid_utf8_and_truncated_archives(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid-utf8.tgz"
    with tarfile.open(invalid_utf8, "w:gz") as archive:
        payload = b'{"name":"pkg","version":"\\xff"}'
        member = tarfile.TarInfo("package/package.json")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    truncated = tmp_path / "truncated.tgz"
    truncated.write_bytes(b"\\x1f\\x8b\\x08truncated")
    valid = tmp_path / "valid.tgz"
    write_tgz(valid)

    result = scan_archives(tmp_path)

    assert [artifact.source_path for artifact in result.artifacts] == [valid]
    assert {issue.source_path for issue in result.issues} == {invalid_utf8, truncated}


def test_inspect_rejects_unsafe_url_components(tmp_path: Path) -> None:
    for index, metadata in enumerate(
        ({"name": "pkg", "version": ".."}, {"name": "pkg", "version": "  "}, {"name": "..", "version": "1.0.0"})
    ):
        path = tmp_path / f"unsafe-{index}.tgz"
        write_tgz(path, metadata)
        with pytest.raises(ArchiveValidationError):
            inspect_archive(path)


def test_inspect_rejects_duplicate_package_metadata_members(tmp_path: Path) -> None:
    path = tmp_path / "duplicate-members.tgz"
    with tarfile.open(path, "w:gz") as archive:
        for version in (b"1.0.0", b"2.0.0"):
            payload = b'{"name":"pkg","version":"' + version + b'"}'
            member = tarfile.TarInfo("package/package.json")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    with pytest.raises(ArchiveValidationError, match="multiple"):
        inspect_archive(path)


def test_scan_rejects_symlink_package_json(tmp_path: Path) -> None:
    path = tmp_path / "symlink.tgz"
    write_tgz(path, symlink=True)

    with pytest.raises(ArchiveValidationError, match="symlink"):
        inspect_archive(path)

    result = scan_archives(tmp_path)
    assert not result.artifacts
    assert result.issues[0].source_path == path


def test_scan_deduplicates_same_name_and_version_deterministically(tmp_path: Path) -> None:
    first = tmp_path / "a" / "first.tgz"
    second = tmp_path / "b" / "second.tgz"
    write_tgz(first, {"name": "pkg", "version": "1.0.0"})
    write_tgz(second, {"name": "pkg", "version": "1.0.0"})

    result = scan_archives(tmp_path)

    assert [artifact.source_path for artifact in result.artifacts] == [first]
    assert [issue.source_path for issue in result.source_duplicates] == [second]
    assert "duplicate" in result.source_duplicates[0].reason
