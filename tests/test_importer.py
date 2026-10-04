from pathlib import Path

import pytest

from gitflic_package.archive import ArchiveIssue, PackageArtifact, ScanResult
from gitflic_package.client import DuplicateError
from gitflic_package.importer import InventoryError, run_import


class FakeClient:
    def __init__(self, inventory: set[tuple[str, str]] | Exception = None) -> None:
        self.inventory = set() if inventory is None else inventory
        self.uploads: list[PackageArtifact] = []
        self.fail_for: set[str] = set()

    def list_package_versions(self) -> set[tuple[str, str]]:
        if isinstance(self.inventory, Exception):
            raise self.inventory
        return self.inventory

    def upload(self, artifact: PackageArtifact) -> None:
        self.uploads.append(artifact)
        if artifact.full_name in self.fail_for:
            raise RuntimeError("upload failed")


def item(tmp_path: Path, name: str, version: str) -> PackageArtifact:
    path = tmp_path / f"{name.replace('@', '').replace('/', '-')}-{version}.tgz"
    path.write_bytes(b"archive")
    scope, _, package_name = name.partition("/")
    return PackageArtifact(
        source_path=path,
        filename=path.name,
        full_name=name,
        package_name=package_name if package_name else name,
        package_scope=scope[1:] if package_name else None,
        version=version,
    )


def test_run_import_skips_existing_versions(tmp_path: Path) -> None:
    existing = item(tmp_path, "pkg", "1.0.0")
    new = item(tmp_path, "other", "1.0.0")
    client = FakeClient({("pkg", "1.0.0")})

    summary = run_import(ScanResult((existing, new), (), ()), client)

    assert [result.status for result in summary.results] == ["uploaded", "skipped"]
    assert summary.skipped == 1
    assert summary.uploaded == 1
    assert summary.failed == 0
    assert summary.ok is True
    assert client.uploads == [new]


def test_run_import_dry_run_never_calls_upload(tmp_path: Path) -> None:
    new = item(tmp_path, "pkg", "1.0.0")
    client = FakeClient()

    summary = run_import(ScanResult((new,), (), ()), client, dry_run=True)

    assert summary.results[0].status == "planned"
    assert summary.planned == 1
    assert client.uploads == []


def test_run_import_uploads_new_artifacts_and_counts_results(tmp_path: Path) -> None:
    first = item(tmp_path, "a", "1.0.0")
    second = item(tmp_path, "b", "2.0.0")

    summary = run_import(ScanResult((second, first), (), ()), FakeClient())

    assert [result.name for result in summary.results] == ["a", "b"]
    assert summary.uploaded == 2
    assert summary.inventory_requests == 1
    assert summary.ok is True


def test_run_import_preserves_request_attempt_counts(tmp_path: Path) -> None:
    class CountingClient(FakeClient):
        last_inventory_requests = 3
        last_request_attempts = 4

    package = item(tmp_path, "pkg", "1.0.0")
    summary = run_import(ScanResult((package,), (), ()), CountingClient())

    assert summary.inventory_requests == 3
    assert summary.results[0].attempts == 4


def test_run_import_reports_zero_attempts_for_pre_request_failure(tmp_path: Path) -> None:
    class SourceReadFailureClient(FakeClient):
        last_request_attempts = 4

        def upload(self, artifact: PackageArtifact) -> None:
            self.last_request_attempts = 0
            artifact.source_path.read_bytes()

    missing = item(tmp_path, "missing", "1.0.0")
    missing.source_path.unlink()

    summary = run_import(ScanResult((missing,), (), ()), SourceReadFailureClient())

    assert summary.results[0].status == "failed"
    assert summary.results[0].attempts == 0


def test_run_import_continues_after_one_upload_failure(tmp_path: Path) -> None:
    failed = item(tmp_path, "failed", "1.0.0")
    successful = item(tmp_path, "successful", "1.0.0")
    client = FakeClient()
    client.fail_for.add("failed")

    summary = run_import(ScanResult((failed, successful), (), ()), client)

    assert [result.status for result in summary.results] == ["failed", "uploaded"]
    assert summary.failed == 1
    assert summary.uploaded == 1
    assert summary.ok is False
    assert client.uploads == [failed, successful]


def test_run_import_carries_invalid_and_source_duplicate_results(tmp_path: Path) -> None:
    valid = item(tmp_path, "valid", "1.0.0")
    invalid_path = tmp_path / "invalid.tgz"
    duplicate_path = tmp_path / "duplicate.tgz"
    scan = ScanResult(
        (valid,),
        (ArchiveIssue(invalid_path, "bad metadata"),),
        (ArchiveIssue(duplicate_path, "duplicate source package version"),),
    )

    summary = run_import(scan, FakeClient())

    assert [result.status for result in summary.results] == ["source-duplicate", "invalid", "uploaded"]
    assert summary.invalid == 1
    assert summary.source_duplicates == 1
    assert summary.ok is False


def test_run_import_does_not_upload_when_inventory_fails(tmp_path: Path) -> None:
    item_to_upload = item(tmp_path, "pkg", "1.0.0")
    client = FakeClient(RuntimeError("inventory unavailable"))

    with pytest.raises(InventoryError):
        run_import(ScanResult((item_to_upload,), (), ()), client)

    assert client.uploads == []
