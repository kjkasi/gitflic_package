import io
import json
import tarfile
from pathlib import Path

import gitflic_package.cli as cli
from gitflic_package.client import HttpResponse


class FakeTransport:
    def __init__(self, *, upload_status: int = 200, upload_body: bytes = b"") -> None:
        self.upload_status = upload_status
        self.upload_body = upload_body
        self.methods: list[str] = []

    def request(self, method, url, headers, body, timeout):
        self.methods.append(method)
        if method == "GET":
            payload = {
                "page": {"totalPages": 1},
                "_embedded": {"simplePackageInfoModelList": []},
            }
            return HttpResponse(200, {}, json.dumps(payload).encode())
        return HttpResponse(self.upload_status, {}, self.upload_body)


def write_config(tmp_path: Path, source_dir: Path, *, max_retries: int = 0) -> Path:
    config = {
        "source_dir": str(source_dir),
        "gitflic": {
            "base_url": "https://registry.gitflic.ru",
            "owner_alias": "team",
            "project_alias": "npm-cache",
            "token_env": "GITFLIC_TOKEN",
            "timeout_seconds": 1,
            "page_size": 100,
        },
        "rate_limit": {"min_interval_seconds": 0, "max_retries": max_retries},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def write_valid_archive(path: Path) -> None:
    payload = b'{"name":"pkg","version":"1.0.0"}'
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo("package/package.json")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))


def test_validate_returns_nonzero_for_invalid_archive(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "invalid.tgz").write_bytes(b"not a tarball")
    config = write_config(tmp_path, source)

    result = cli.main(["validate", "--config", str(config)])

    captured = capsys.readouterr()
    assert result == 1
    assert "invalid" in captured.out.lower()


def test_validate_does_not_require_token(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_valid_archive(source / "pkg.tgz")
    config = write_config(tmp_path, source)
    monkeypatch.delenv("GITFLIC_TOKEN", raising=False)

    assert cli.main(["validate", "--config", str(config)]) == 0


def test_import_dry_run_returns_zero_and_performs_no_put(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_valid_archive(source / "pkg.tgz")
    config = write_config(tmp_path, source)
    transport = FakeTransport()
    monkeypatch.setenv("GITFLIC_TOKEN", "SECRET")
    monkeypatch.setattr(cli, "UrlLibTransport", lambda: transport)

    result = cli.main(["import", "--config", str(config), "--dry-run"])

    assert result == 0
    assert transport.methods == ["GET"]
    assert "planned" in capsys.readouterr().out.lower()


def test_import_returns_nonzero_for_upload_failure(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_valid_archive(source / "pkg.tgz")
    config = write_config(tmp_path, source)
    transport = FakeTransport(upload_status=500)
    monkeypatch.setenv("GITFLIC_TOKEN", "SECRET")
    monkeypatch.setattr(cli, "UrlLibTransport", lambda: transport)

    assert cli.main(["import", "--config", str(config)]) == 1
    assert transport.methods == ["GET", "PUT"]


def test_missing_network_source_reports_clear_error(tmp_path: Path, capsys) -> None:
    config = write_config(tmp_path, tmp_path / "\\\\server\\verdaccio\\storage")

    result = cli.main(["validate", "--config", str(config)])

    assert result == 1
    assert "source_dir" in capsys.readouterr().err


def test_cli_output_never_contains_token(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "source"
    source.mkdir()
    write_valid_archive(source / "pkg.tgz")
    config = write_config(tmp_path, source)
    transport = FakeTransport(upload_status=403, upload_body=b"SECRET")
    monkeypatch.setenv("GITFLIC_TOKEN", "SECRET")
    monkeypatch.setattr(cli, "UrlLibTransport", lambda: transport)

    assert cli.main(["import", "--config", str(config), "--verbose"]) == 1
    captured = capsys.readouterr()
    assert "SECRET" not in captured.out
    assert "SECRET" not in captured.err
