# GitFlic npm package migration design

**Status:** Approved design

## Goal

Provide a standalone Python CLI that imports npm package tarballs from a copied Verdaccio storage directory into one GitFlic project registry, safely and repeatably.

## Scope

The first version is a migration/import utility, not a general npm client. It supports:

- Recursive discovery of `.tgz` files under a local or UNC/network source directory.
- Reading package `name` and `version` from `package/package.json` inside each tarball.
- Validation and dry-run planning.
- Idempotent upload to one configured GitFlic project package registry.
- Skipping package versions already present in GitFlic.
- Sequential, rate-limited requests suitable for a long-running migration.
- Per-artifact results and a final summary.

It does not delete packages, overwrite versions, create GitFlic projects, or manage multiple destinations.

## External GitFlic contract

The implementation targets the documented project package API:

- List project packages: `GET /registry/project/{ownerAlias}/{projectAlias}/package`.
- Upload an unscoped npm package: `PUT /registry/project/{ownerAlias}/{projectAlias}/package/npm/{packageName}/{packageVersion}/{fileName}`.
- Upload a scoped npm package: `PUT /registry/project/{ownerAlias}/{projectAlias}/package/npm/{packageName}/{packageScope}/{packageVersion}/{fileName}`.
- Successful upload returns HTTP 200.
- The request body is the tarball bytes and the content type is `application/octet-stream`.
- Authentication uses an API access token in `Authorization: token <api-access-token>`.
- The list endpoint is paginated using `page` and `size` query parameters and returns package entries under `_embedded.simplePackageInfoModelList`.

The configured base URL defaults to `https://api.gitflic.ru` and can be changed for self-hosted GitFlic. The API documentation reports a 500-requests-per-hour SaaS API limit; self-hosted instances can configure hourly, per-IP, and per-user limits. No separate npm-specific limit is documented.

## Architecture

Runtime dependencies use the Python standard library. The code is separated into these units:

- `gitflic_package/config.py`: load and validate JSON configuration, resolve the token environment variable, and provide typed configuration values.
- `gitflic_package/archive.py`: recursively discover tarballs, safely inspect `package/package.json`, validate npm metadata, and return package records without extracting files.
- `gitflic_package/client.py`: construct GitFlic project URLs, perform authenticated GET/PUT requests, paginate the existing package inventory, and classify HTTP errors.
- `gitflic_package/limiter.py`: enforce the configured minimum interval across every request and implement bounded retry/backoff behavior.
- `gitflic_package/importer.py`: coordinate discovery, destination inventory, duplicate decisions, uploads, and per-artifact results.
- `gitflic_package/cli.py`: implement `validate` and `import` commands, output summaries, and map results to exit codes.

The package entry point is `python -m gitflic_package`.

## Configuration

The configuration file is JSON:

```json
{
  "source_dir": "\\\\server\\verdaccio\\storage",
  "gitflic": {
    "base_url": "https://api.gitflic.ru",
    "owner_alias": "team",
    "project_alias": "npm-cache",
    "token_env": "GITFLIC_API_TOKEN",
    "timeout_seconds": 60,
    "page_size": 100
  },
  "rate_limit": {
    "min_interval_seconds": 10,
    "max_retries": 5
  }
}
```

Requirements:

- `source_dir` must exist and be a directory; local paths and UNC paths are supported.
- `base_url`, `owner_alias`, and `project_alias` must be nonempty; trailing slashes are normalized.
- `token_env` names the environment variable containing the API access token. The token must be present and nonempty before any GitFlic request.
- `timeout_seconds` and `page_size` must be positive.
- `min_interval_seconds` must be nonnegative. The default is 10 seconds, approximately 360 requests/hour, leaving headroom below the documented 500/hour SaaS API limit.
- `max_retries` must be nonnegative. The default is 5.

The token is never accepted as a CLI argument, stored in the config, logged, or included in exception text.

## CLI

```text
python -m gitflic_package validate --config config.json [--verbose]
python -m gitflic_package import --config config.json [--dry-run] [--verbose]
```

`validate` scans and inspects archives only; it does not require GitFlic credentials or contact GitFlic. It returns nonzero if any archive is invalid.

`import --dry-run` validates archives, loads the GitFlic inventory, and reports `upload` or `skip` decisions without issuing upload PUT requests. It returns nonzero for configuration, inventory, or artifact errors.

`import` performs the same planning and uploads new artifacts. It continues after an individual package failure and returns nonzero if any upload fails.

Output is human-readable by default. `--verbose` includes request-independent diagnostic details such as source paths, retry counts, and response status; it must still redact tokens.

## Archive processing

Discovery recursively finds files whose case-insensitive suffix is `.tgz`, in deterministic sorted path order. Each archive is opened in read mode and must contain a regular file at `package/package.json`; no archive extraction is required.

The parser validates that:

- the JSON is an object;
- `name` and `version` are nonempty strings;
- the npm name is either unscoped (`name`) or scoped (`@scope/name`);
- the version is a nonempty npm version string suitable for a URL path;
- the package JSON member is not a symlink and is within the archive.

A package record contains the source `Path`, archive filename, package name, version, optional scope, and URL-safe path components. The upload filename is the original `.tgz` basename. Invalid archives are reported without stopping discovery.

If multiple source archives resolve to the same `(name, version)`, the first deterministic path is selected and the remaining records are reported as source duplicates rather than uploaded twice.

## Import flow

1. Load and validate configuration.
2. Discover and parse all source tarballs.
3. For import, create an authenticated GitFlic client and retrieve all inventory pages.
4. Build an existing `(name, version)` set from the returned package records.
5. Mark records already in the set as `skipped`.
6. In sorted order, upload each remaining valid record using the scoped or unscoped endpoint.
7. Emit per-record results and counts for uploaded, skipped, invalid, source duplicate, and failed records.

Existing package versions are skipped as successful idempotent outcomes. The importer never overwrites or deletes an existing version. A duplicate discovered after planning (for example, a race with another uploader) is also treated as skipped when GitFlic identifies it as an existing version.

## Rate limiting and retries

All GitFlic GET and PUT requests pass through one sequential limiter. The default minimum interval is 10 seconds, with no burst allowance or parallel uploads. This is deliberately below the documented SaaS API limit rather than assuming a per-second npm quota.

For HTTP 429 responses, the client honors `Retry-After` when supplied. If it is absent or invalid, it uses bounded exponential backoff. Transient 5xx responses and transport timeouts also use bounded exponential backoff, while 4xx responses other than a recognized duplicate are not retried.

A retry still passes through the limiter. After `max_retries`, the artifact is failed and the importer continues with later artifacts. Retry counts and throttling are included in the summary but never include credentials.

## Error handling

- Missing or invalid configuration/token: fail before any GitFlic request.
- Missing source directory: fail before import.
- Invalid tarball/package metadata: report invalid and continue scanning.
- HTTP 403: report an authentication or `READ_REGISTRY`/`CREATE_PACKAGE` API-token permission problem.
- HTTP 404: report an invalid GitFlic base URL, owner, project, or destination.
- HTTP 409 or a recognizable already-exists response: report skipped where safe.
- HTTP 429, transient 5xx, and timeouts: retry according to the limiter policy, then fail the affected artifact.
- Other HTTP failures: report the status and a bounded response summary, never raw credentials.
- A failed inventory request prevents uploading because duplicate safety cannot be guaranteed.

## Verification

The test suite will run without a GitFlic account using temporary directories, generated tarballs, and a local fake HTTP server or mocked transport. It will cover:

- recursive and deterministic `.tgz` discovery;
- valid unscoped and scoped `package.json` metadata;
- malformed archives, missing metadata, invalid metadata, symlink rejection, and source duplicates;
- correct project list pagination and `(name, version)` inventory construction;
- correct scoped/unscoped URL and authorization header construction;
- dry-run issuing no PUT requests;
- successful uploads and result summaries;
- duplicate skips and race-condition duplicate responses;
- 403, 404, 429, 5xx, timeout, retry, and partial-failure behavior;
- configured interval enforcement and `Retry-After` handling;
- absence of the token from logs and error messages;
- CLI exit codes for validation, dry-run, and import outcomes.

## Acceptance criteria

The feature is complete when an operator can point the JSON config at a copied Verdaccio storage directory, export the configured GitFlic API access token, run dry-run, review upload/skip decisions, and run import to migrate all valid missing package versions. Re-running the command does not re-upload existing versions, respects the configured request interval, produces a useful summary, and returns a failing exit code only when the migration has unresolved validation, inventory, or upload errors.

## References

- GitFlic NPM registry: https://docs.gitflic.ru/latest/en/registry/npm/
- GitFlic package API: https://docs.gitflic.ru/latest/en/api/package/
- GitFlic API introduction and limits: https://docs.gitflic.ru/latest/en/api/intro/
- GitFlic administrator rate-limit settings: https://docs.gitflic.ru/latest/en/admin_panel/settings/
