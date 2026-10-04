# GitFlic npm package migration

A standalone Python 3.10+ CLI for importing npm `.tgz` files from copied Verdaccio storage into one GitFlic project registry. Runtime code uses only the Python standard library.

## Install

```sh
python -m pip install -e .
```

## Configuration

Create a JSON file pointing at the source directory and GitFlic project:

```json
{
  "source_dir": "\\\\server\\verdaccio\\storage",
  "gitflic": {
    "base_url": "https://registry.gitflic.ru",
    "owner_alias": "team",
    "project_alias": "npm-cache",
    "token_env": "GITFLIC_TOKEN",
    "timeout_seconds": 60,
    "page_size": 100
  },
  "rate_limit": {
    "min_interval_seconds": 10,
    "max_retries": 5
  }
}
```

`source_dir` may be a local or UNC/network path and must be an existing directory. `base_url` defaults to `https://registry.gitflic.ru`; `timeout_seconds` and `page_size` must be positive. The default request interval is 10 seconds, approximately 360 requests per hour.

The transport token is read only from the configured environment variable. It is never accepted as a command-line argument or stored in the JSON file.

PowerShell:

```powershell
$env:GITFLIC_TOKEN = "<transport-token>"
```

POSIX shells:

```sh
export GITFLIC_TOKEN='<transport-token>'
```

## Commands

Validate local archives without credentials or network access:

```sh
python -m gitflic_package validate --config config.json
python -m gitflic_package validate --config config.json --verbose
```

Plan an import. Dry-run loads the destination inventory but never sends upload `PUT` requests:

```sh
python -m gitflic_package import --config config.json --dry-run
```

Run the migration:

```sh
python -m gitflic_package import --config config.json
```

The importer recursively discovers `.tgz` files, validates `package/package.json`, reports invalid archives, skips existing `(package, version)` records, and uploads new versions sequentially. Re-running the command is safe and idempotent: existing versions are not overwritten or deleted. A nonzero exit code indicates invalid input, an inventory failure, or an unresolved upload failure.

## Rate limits

All inventory and upload requests share one sequential limiter. The default 10-second interval leaves headroom below the documented GitFlic SaaS API limit of 500 requests per hour. Self-hosted GitFlic installations may configure hourly, per-IP, and per-user limits independently; adjust `min_interval_seconds` and retry settings to match the destination.

HTTP 429 responses honor a valid `Retry-After` value. Missing or malformed retry metadata, transient 5xx responses, and transport timeouts use bounded exponential backoff. Credentials are redacted from diagnostics.
