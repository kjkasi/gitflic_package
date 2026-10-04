import json
from pathlib import Path

import pytest

from gitflic_package.archive import PackageArtifact
from gitflic_package.client import (
    AuthenticationError,
    DestinationError,
    DuplicateError,
    GitFlicClient,
    HttpResponse,
)
from gitflic_package.config import GitFlicConfig
from gitflic_package.limiter import RateLimiter


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict[str, str], bytes | None, float]] = []

    def request(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers), body, timeout))
        if not self.responses:
            raise AssertionError("unexpected request")
        return self.responses.pop(0)


def response(status: int, payload: object | None = None, headers: dict[str, str] | None = None) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers=headers or {},
        body=b"" if payload is None else json.dumps(payload).encode("utf-8"),
    )


def config() -> GitFlicConfig:
    return GitFlicConfig(
        base_url="https://registry.gitflic.ru/",
        owner_alias="team",
        project_alias="npm-cache",
        token_env="GITFLIC_TOKEN",
        timeout_seconds=12.5,
        page_size=2,
    )


def artifact(tmp_path: Path, *, scoped: bool = False) -> PackageArtifact:
    path = tmp_path / ("scoped.tgz" if scoped else "pkg-1.0.0.tgz")
    path.write_bytes(b"tarball-bytes")
    return PackageArtifact(
        source_path=path,
        filename=path.name,
        full_name="@scope/pkg" if scoped else "pkg",
        package_name="pkg",
        package_scope="scope" if scoped else None,
        version="1.0.0",
    )


def make_client(transport: FakeTransport) -> GitFlicClient:
    return GitFlicClient(config(), "SECRET", RateLimiter(0.0), transport)


def test_list_package_versions_follows_pagination() -> None:
    transport = FakeTransport(
        [
            response(200, {"page": {"totalPages": 2}, "_embedded": {"simplePackageInfoModelList": [{"name": "pkg", "version": "1.0.0"}]}}),
            response(200, {"page": {"totalPages": 2}, "_embedded": {"simplePackageInfoModelList": [{"name": "@scope/pkg", "version": "2.0.0"}]}}),
        ]
    )

    versions = make_client(transport).list_package_versions()

    assert versions == {("pkg", "1.0.0"), ("@scope/pkg", "2.0.0")}
    assert len(transport.requests) == 2
    assert "page=0" in transport.requests[0][1]
    assert "page=1" in transport.requests[1][1]
    assert all(request[2]["Authorization"] == "token SECRET" for request in transport.requests)


def test_upload_builds_unscoped_url_and_token_header(tmp_path: Path) -> None:
    transport = FakeTransport([response(200)])
    item = artifact(tmp_path)

    make_client(transport).upload(item)

    method, url, headers, body, timeout = transport.requests[0]
    assert method == "PUT"
    assert url.endswith("/registry/project/team/npm-cache/package/npm/pkg/1.0.0/pkg-1.0.0.tgz")
    assert headers["Authorization"] == "token SECRET"
    assert headers["Content-Type"] == "application/octet-stream"
    assert body == b"tarball-bytes"
    assert timeout == 12.5


def test_upload_builds_scoped_url_without_leading_at(tmp_path: Path) -> None:
    transport = FakeTransport([response(200)])

    make_client(transport).upload(artifact(tmp_path, scoped=True))

    assert transport.requests[0][1].endswith(
        "/registry/project/team/npm-cache/package/npm/pkg/scope/1.0.0/scoped.tgz"
    )
    assert "@scope" not in transport.requests[0][1]


def test_client_retries_429_and_honors_retry_after(tmp_path: Path) -> None:
    transport = FakeTransport(
        [response(429, headers={"Retry-After": "3"}), response(200)]
    )
    client = make_client(transport)

    client.upload(artifact(tmp_path))

    assert len(transport.requests) == 2


def test_client_retries_transient_5xx(tmp_path: Path) -> None:
    transport = FakeTransport([response(503), response(200)])

    make_client(transport).upload(artifact(tmp_path))

    assert len(transport.requests) == 2


def test_client_classifies_403_and_404(tmp_path: Path) -> None:
    with pytest.raises(AuthenticationError) as forbidden:
        make_client(FakeTransport([response(403, {"message": "SECRET"})])).upload(artifact(tmp_path))
    assert "SECRET" not in str(forbidden.value)

    with pytest.raises(DestinationError):
        make_client(FakeTransport([response(404)])).upload(artifact(tmp_path))

    with pytest.raises(DuplicateError):
        make_client(FakeTransport([response(409)])).upload(artifact(tmp_path))
