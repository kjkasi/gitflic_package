# GitFlic npm Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Python CLI that safely migrates npm `.tgz` packages from copied Verdaccio storage into one GitFlic project registry.

**Architecture:** Separate configuration, tarball inspection, GitFlic HTTP access, rate limiting, orchestration, and CLI presentation into small modules. Use the standard library at runtime, authenticated direct GitFlic REST calls, deterministic scanning, inventory-based duplicate skips, and sequential uploads.

**Tech Stack:** Python 3.10+, standard-library `argparse`/`json`/`tarfile`/`urllib`, `pytest` for tests, temporary directories and fake transports/local HTTP fixtures.

**Spec:** `docs/superpowers/specs/2026-10-04-gitflic-npm-migration-design.md`

## Global Constraints

- Runtime dependencies use the Python standard library.
- The first version is a migration/import utility, not a general npm client.
- The default minimum interval is 10 seconds, approximately 360 requests/hour, leaving headroom below the documented 500/hour SaaS API limit.
- All GitFlic GET and PUT requests pass through one sequential limiter.
- The token is never accepted as a CLI argument, stored in the config, logged, or included in exception text.
- The importer never overwrites or deletes an existing version.
- Discovery recursively finds files whose case-insensitive suffix is `.tgz`, in deterministic sorted path order.
- A failed inventory request prevents uploading because duplicate safety cannot be guaranteed.
- Runtime code must support local paths and UNC/network paths.

## Review Focus

- **UNC/network source path:** an unavailable or non-directory share must fail with a clear configuration error; covered by `test_load_config_rejects_missing_source_dir` and the CLI configuration-error test in Task 5.
- **Repeated source versions:** multiple tarballs with the same `(name, version)` must produce one deterministic upload candidate and source-duplicate results; covered by Task 2’s duplicate scan test.
- **Scoped package URL components:** `@scope/name` must become the exact GitFlic `packageName= name` and `packageScope= scope` path segments without leaking the leading `@`; covered by Task 3’s URL test.
- **Malformed throttling metadata:** invalid or HTTP-date `Retry-After` values must not crash the client and must fall back to bounded backoff; covered by Task 3’s retry tests.
- **Partial inventory failure:** failure while fetching any inventory page must prevent all uploads and return a nonzero result; covered by Task 4’s orchestration test.

---

### Task 1: Create the package scaffold and configuration loader

**Files:**
- Create: `pyproject.toml`
- Create: `gitflic_package/__init__.py`
- Create: `gitflic_package/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces `GitFlicConfig`, `RateLimitConfig`, and `AppConfig` frozen dataclasses.
- Produces `load_config(path: Path) -> AppConfig`.
- Produces `resolve_token(config: GitFlicConfig, environ: Mapping[str, str] | None = None) -> str`.
- `GitFlicConfig` fields: `base_url: str`, `owner_alias: str`, `project_alias: str`, `token_env: str`, `timeout_seconds: float`, `page_size: int`.
- `RateLimitConfig` fields: `min_interval_seconds: float = 10.0`, `max_retries: int = 5`.
- `AppConfig` fields: `source_dir: Path`, `gitflic: GitFlicConfig`, `rate_limit: RateLimitConfig`.

- [x] **Step 1: Write the failing configuration tests**

Add tests named `test_load_config_applies_defaults_and_normalizes_base_url`, `test_load_config_reads_explicit_rate_limit`, `test_load_config_rejects_missing_source_dir`, `test_load_config_rejects_invalid_positive_values`, and `test_resolve_token_reads_named_environment_variable_and_rejects_missing_token`. Assert the exact JSON shape from the spec, normalized base URL without a trailing slash, default `10.0`/`5`, UNC-compatible `Path` handling, and no token value in validation errors.

- [x] **Step 2: Run the configuration tests to verify failure**

Run: `python -m pytest tests/test_config.py -q`
Expected: FAIL because `gitflic_package.config` and its dataclasses/functions do not exist.

- [x] **Step 3: Implement configuration loading**

Implement `config.py` with `json.load`, `Path`, dataclass validation, required-field checks, positive numeric checks, base URL normalization, and environment lookup. Keep token resolution separate from file loading so `validate` does not need credentials. Add `pyproject.toml` with Python `>=3.10`, pytest test configuration, and no runtime dependencies.

- [x] **Step 4: Run the configuration tests to verify the implementation**

Run: `python -m pytest tests/test_config.py -q`
Expected: PASS.

- [x] **Step 5: Commit the scaffold and configuration**

```bash
git add pyproject.toml gitflic_package/__init__.py gitflic_package/config.py tests/test_config.py
git commit -m "feat: add GitFlic migration configuration"
```

### Task 2: Implement safe Verdaccio tarball discovery and metadata parsing

**Files:**
- Create: `gitflic_package/archive.py`
- Test: `tests/test_archive.py`

**Interfaces:**
- Produces `PackageArtifact` frozen dataclass with `source_path: Path`, `filename: str`, `full_name: str`, `package_name: str`, `package_scope: str | None`, and `version: str`.
- Produces `ArchiveIssue` frozen dataclass with `source_path: Path` and `reason: str`.
- Produces `ScanResult` frozen dataclass with `artifacts: tuple[PackageArtifact, ...]`, `issues: tuple[ArchiveIssue, ...]`, and `source_duplicates: tuple[ArchiveIssue, ...]`.
- Produces `discover_archives(source_dir: Path) -> tuple[Path, ...]`.
- Produces `inspect_archive(path: Path) -> PackageArtifact` and raises `ArchiveValidationError` for invalid archives.
- Produces `scan_archives(source_dir: Path) -> ScanResult`.
- `PackageArtifact` is the input consumed by Tasks 3 and 4.

- [x] **Step 1: Write the failing archive tests**

Create helpers that write valid `.tgz` files with `package/package.json`. Add tests named `test_discover_archives_is_recursive_case_insensitive_and_sorted`, `test_inspect_archive_reads_unscoped_metadata`, `test_inspect_archive_splits_scoped_name`, `test_scan_reports_missing_or_malformed_metadata`, `test_scan_rejects_symlink_package_json`, and `test_scan_deduplicates_same_name_and_version_deterministically`. Assert `@scope/pkg` becomes `package_scope="scope"` and `package_name="pkg"`, invalid files do not stop other files, and only the first sorted duplicate is an artifact.

- [x] **Step 2: Run the archive tests to verify failure**

Run: `python -m pytest tests/test_archive.py -q`
Expected: FAIL because `gitflic_package.archive` does not exist.

- [x] **Step 3: Implement archive discovery and inspection**

Use `Path.rglob` with a case-insensitive `.tgz` check and sorted paths. Inspect tar members without extracting, require a regular `package/package.json` member, parse JSON, validate object/name/version/scoped-name shape, and reject unsafe or symlink metadata members. Return issues instead of aborting the full scan; group duplicate `(full_name, version)` records deterministically.

- [x] **Step 4: Run the archive tests to verify the implementation**

Run: `python -m pytest tests/test_archive.py -q`
Expected: PASS.

- [x] **Step 5: Commit archive processing**

```bash
git add gitflic_package/archive.py tests/test_archive.py
git commit -m "feat: scan Verdaccio npm tarballs"
```

### Task 3: Implement GitFlic HTTP operations and conservative request limiting

**Files:**
- Create: `gitflic_package/limiter.py`
- Create: `gitflic_package/client.py`
- Test: `tests/test_limiter.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Produces `HttpResponse` with `status: int`, `headers: Mapping[str, str]`, and `body: bytes`.
- Produces `HttpTransport` protocol with `request(method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float) -> HttpResponse`.
- Produces `UrlLibTransport`, the production `urllib` implementation of `HttpTransport`.
- Produces `RateLimiter(min_interval_seconds: float, clock: Callable[[], float] = ..., sleeper: Callable[[float], None] = ...)` with `wait() -> None`.
- Produces `parse_retry_after(value: str | None, now: datetime | None = None) -> float | None` and `backoff_seconds(attempt: int, retry_after: str | None, now: datetime | None = None) -> float`.
- Produces `GitFlicClient(config: GitFlicConfig, token: str, rate_limiter: RateLimiter, transport: HttpTransport)`.
- `GitFlicClient.list_package_versions() -> set[tuple[str, str]]` paginates until `page.totalPages` and returns `(name, version)` keys.
- `GitFlicClient.package_upload_url(artifact: PackageArtifact) -> str` builds scoped/unscoped documented paths.
- `GitFlicClient.upload(artifact: PackageArtifact) -> None` performs an authenticated raw-byte PUT.
- Produces `GitFlicError` subclasses for authentication, destination, duplicate, retry exhaustion, and other HTTP failures.

- [x] **Step 1: Write failing limiter and client tests**

Add fake clock/sleeper and fake transport fixtures. Test `test_rate_limiter_waits_between_requests`, `test_retry_after_accepts_seconds_and_http_date`, `test_invalid_retry_after_uses_bounded_backoff`, `test_list_package_versions_follows_pagination`, `test_upload_builds_unscoped_url_and_token_header`, `test_upload_builds_scoped_url_without_leading_at`, `test_client_retries_429_and_honors_retry_after`, `test_client_retries_transient_5xx`, and `test_client_classifies_403_and_404`. Assert all requests carry `Authorization: token SECRET`, PUT uses `application/octet-stream`, inventory failure raises before any upload, and the token does not appear in raised error text.

- [x] **Step 2: Run the client tests to verify failure**

Run: `python -m pytest tests/test_limiter.py tests/test_client.py -q`
Expected: FAIL because `gitflic_package.limiter` and `gitflic_package.client` do not exist.

- [x] **Step 3: Implement the rate limiter**

Implement monotonic minimum-interval enforcement with injectable clock/sleeper for deterministic tests. Parse integer-second and HTTP-date `Retry-After` values; use bounded exponential fallback for malformed/missing values. Ensure retries call the same limiter before each request and never introduce parallel or burst requests.

- [x] **Step 4: Implement the GitFlic transport and client**

Implement `UrlLibTransport` with raw request bodies, bounded response reads, timeout forwarding, and HTTP error conversion. Build URLs with URL quoting while preserving path separators, use `/registry/project/{owner}/{project}/package`, follow `page`/`size` pagination, and upload to the scoped or unscoped npm endpoint. Classify 403/404/409/429/5xx and redact tokens from error messages.

- [x] **Step 5: Run the limiter and client tests to verify the implementation**

Run: `python -m pytest tests/test_limiter.py tests/test_client.py -q`
Expected: PASS.

- [x] **Step 6: Commit the GitFlic client**

```bash
git add gitflic_package/limiter.py gitflic_package/client.py tests/test_limiter.py tests/test_client.py
git commit -m "feat: add rate-limited GitFlic package client"
```

### Task 4: Implement import planning, duplicate skips, and partial-failure orchestration

**Files:**
- Create: `gitflic_package/importer.py`
- Test: `tests/test_importer.py`

**Interfaces:**
- Consumes `ScanResult`, `PackageArtifact`, and `GitFlicClient` from Tasks 2 and 3.
- Produces `ArtifactResult` frozen dataclass with `source_path: Path`, `name: str`, `version: str`, `status: Literal["uploaded", "skipped", "planned", "invalid", "source-duplicate", "failed"]`, `attempts: int`, and `message: str`.
- Produces `ImportSummary` frozen dataclass with `results: tuple[ArtifactResult, ...]`, `inventory_requests: int`, `uploaded: int`, `skipped: int`, `planned: int`, `invalid: int`, `source_duplicates: int`, `failed: int`, and `ok: bool`.
- Produces `InventoryError`, raised when destination inventory cannot be loaded safely.
- Produces `run_import(scan: ScanResult, client: GitFlicClient, dry_run: bool = False) -> ImportSummary`.
- `run_import` calls inventory before any upload, marks existing versions skipped, marks new versions planned during dry-run, continues after individual upload failures, and raises `InventoryError` without uploading when inventory cannot be retrieved.

- [x] **Step 1: Write the failing importer tests**

Add `test_run_import_skips_existing_versions`, `test_run_import_dry_run_never_calls_upload`, `test_run_import_uploads_new_artifacts_and_counts_results`, `test_run_import_continues_after_one_upload_failure`, `test_run_import_carries_invalid_and_source_duplicate_results`, and `test_run_import_does_not_upload_when_inventory_fails`. Assert exact statuses/counts, deterministic order, `ok=False` for unresolved upload failures, and `pytest.raises(InventoryError)` with zero upload calls when inventory fails.

- [x] **Step 2: Run the importer tests to verify failure**

Run: `python -m pytest tests/test_importer.py -q`
Expected: FAIL because `gitflic_package.importer` does not exist.

- [x] **Step 3: Implement import orchestration**

Load the destination set once through `list_package_versions()`, convert scan issues into result records, decide by `(full_name, version)`, and process valid new artifacts in sorted order. Treat recognized duplicate upload errors as skipped; convert other exceptions into failed results while continuing. Keep dry-run free of PUT calls and make `ok` false for invalid/failed/inventory-error outcomes.

- [x] **Step 4: Run the importer tests to verify the implementation**

Run: `python -m pytest tests/test_importer.py -q`
Expected: PASS.

- [x] **Step 5: Commit import orchestration**

```bash
git add gitflic_package/importer.py tests/test_importer.py
git commit -m "feat: orchestrate idempotent package imports"
```

### Task 5: Add the CLI entry point, documentation, and end-to-end verification

**Files:**
- Create: `gitflic_package/cli.py`
- Create: `gitflic_package/__main__.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces `build_parser() -> argparse.ArgumentParser`.
- Produces `main(argv: Sequence[str] | None = None) -> int`.
- `python -m gitflic_package validate --config config.json [--verbose]` performs local validation.
- `python -m gitflic_package import --config config.json [--dry-run] [--verbose]` performs planning or import.
- Consumes `load_config`, `resolve_token`, `scan_archives`, `GitFlicClient`, and `run_import` from previous tasks.

- [x] **Step 1: Write the failing CLI tests**

Add `test_validate_returns_nonzero_for_invalid_archive`, `test_validate_does_not_require_token`, `test_import_dry_run_returns_zero_and_performs_no_put`, `test_import_returns_nonzero_for_upload_failure`, `test_missing_network_source_reports_clear_error`, and `test_cli_output_never_contains_token`. Assert command parsing, summary labels, exit codes, dry-run behavior, and redaction.

- [x] **Step 2: Run the CLI tests to verify failure**

Run: `python -m pytest tests/test_cli.py -q`
Expected: FAIL because `gitflic_package.cli` and `gitflic_package.__main__` do not exist.

- [x] **Step 3: Implement CLI dispatch and output**

Use `argparse` subcommands, load configuration once, run local validation without token resolution, resolve the token only for import, construct the client with the configured limiter/transport, print per-artifact and aggregate results, and convert expected errors to concise stderr messages and nonzero exit codes. Ensure verbose output contains no token. Make `__main__.py` call `main()` through `SystemExit`.

- [x] **Step 4: Document installation, configuration, token setup, rate limits, and examples**

Replace the current title-only README with Python version requirements, JSON config, `GITFLIC_TOKEN` export examples for Windows PowerShell and POSIX shells, validate/dry-run/import commands, expected 10-second default pacing, rerun/idempotency behavior, and the documented SaaS/self-hosted rate-limit distinction. Do not place a real token in examples.

- [x] **Step 5: Run the full test suite and static checks**

Run: `python -m pytest -q && python -m compileall gitflic_package && git diff --check`
Expected: all tests PASS, compilation succeeds, and `git diff --check` produces no output.

- [x] **Step 6: Commit the CLI and documentation**

```bash
git add gitflic_package/cli.py gitflic_package/__main__.py README.md tests/test_cli.py
git commit -m "feat: add GitFlic npm migration CLI"
```

## Final Verification

After all tasks, run:

```bash
python -m pytest -q
python -m gitflic_package --help
python -m compileall gitflic_package
```

Expected: the complete test suite passes, help lists `validate` and `import`, compilation succeeds, and no credentials or unrelated files are changed.
