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

`source_dir` may be a local or UNC/network path and must be an existing directory. `base_url` defaults to `https://api.gitflic.ru`; `timeout_seconds` and `page_size` must be positive.

### Migrating existing configuration

Configurations that omit `base_url` now use the REST API endpoint
`https://api.gitflic.ru`; configurations that omit `token_env` now use
`GITFLIC_API_TOKEN`. If an existing configuration relied on the former implicit
`GITFLIC_TOKEN` default, set `token_env` explicitly and export an API access
token in that variable before importing. Existing configurations with an
explicit `token_env` continue to use that variable, but its value must be an
API access token.

### Authentication and token choice

This CLI calls the GitFlic **REST package API** at `/registry/...`; it is not an npm client.
Use an **API access token** created in GitFlic under **Profile settings → API Tokens**.
The token needs permission to read the project registry and create packages.
It is sent as `Authorization: token <api-access-token>`.

The configured `token_env` names the environment variable containing that API access token. The token is never accepted as a command-line argument or stored in the JSON file.

PowerShell:

```powershell
$env:GITFLIC_API_TOKEN = "<api-access-token>"
```

POSIX shells:

```sh
export GITFLIC_API_TOKEN='<api-access-token>'
```

Do **not** put a transport token in `GITFLIC_API_TOKEN`. A transport token is a
separate credential created under **Profile settings → Transport Tokens** for the
npm/package registry transport. It belongs in npm configuration as `_authToken`
for a registry URL such as:

```ini
registry=https://registry.gitflic.ru/project/<owner>/<project>/package/-/npm/
//registry.gitflic.ru/project/<owner>/<project>/package/-/npm/:_authToken=<transport-token>
```

For this CLI, do not use `https://gitflic.ru` (the web UI) or
`https://registry.gitflic.ru` as `base_url`. The first redirects unauthenticated
requests to `/auth/login` with HTTP `302`; the second is the npm registry host.
Use `https://api.gitflic.ru` for SaaS, or the REST API base URL of your self-hosted
GitFlic instance. A `302` from this CLI usually means the web UI URL was configured
instead of the REST API URL.

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
