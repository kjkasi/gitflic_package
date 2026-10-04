"""Authenticated GitFlic project package API client."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .archive import PackageArtifact
from .config import GitFlicConfig
from .limiter import RateLimiter, backoff_seconds


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse: ...


class UrlLibTransport:
    """Standard-library HTTP transport retaining response bodies for errors."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                return HttpResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(1024 * 1024),
                )
        except HTTPError as error:
            return HttpResponse(
                status=error.code,
                headers=dict(error.headers.items()),
                body=error.read(1024 * 1024),
            )
        except URLError as error:
            raise TransportError("GitFlic request failed") from error
        except TimeoutError as error:
            raise TransportError("GitFlic request timed out") from error


class GitFlicError(RuntimeError):
    """Base class for safe, transport-level GitFlic errors."""


class AuthenticationError(GitFlicError):
    pass


class DestinationError(GitFlicError):
    pass


class DuplicateError(GitFlicError):
    pass


class RetryExhaustedError(GitFlicError):
    pass


class TransportError(GitFlicError):
    pass


class HttpStatusError(GitFlicError):
    pass


class GitFlicClient:
    def __init__(
        self,
        config: GitFlicConfig,
        token: str,
        rate_limiter: RateLimiter,
        transport: HttpTransport,
    ) -> None:
        self.config = config
        self.token = token
        self.rate_limiter = rate_limiter
        self.transport = transport

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"token {self.token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _safe_body(self, response: HttpResponse) -> str:
        message = response.body[:512].decode("utf-8", errors="replace")
        return message.replace(self.token, "[REDACTED]")

    def _request(
        self, method: str, url: str, body: bytes | None = None, content_type: str | None = None
    ) -> HttpResponse:
        retry_after: str | None = None
        max_retries = max(0, self.rate_limiter.max_retries)
        for attempt in range(max_retries + 1):
            self.rate_limiter.wait()
            try:
                response = self.transport.request(
                    method,
                    url,
                    self._headers(content_type),
                    body,
                    self.config.timeout_seconds,
                )
            except TransportError:
                if attempt >= max_retries:
                    raise RetryExhaustedError("GitFlic request retries exhausted")
                self.rate_limiter.sleeper(backoff_seconds(attempt + 1, None))
                continue

            retry_after = response.headers.get("Retry-After")
            if response.status == 429 or 500 <= response.status <= 599:
                if attempt >= max_retries:
                    raise RetryExhaustedError(
                        f"GitFlic request retries exhausted (HTTP {response.status})"
                    )
                self.rate_limiter.sleeper(
                    backoff_seconds(attempt + 1, retry_after)
                )
                continue
            return response
        raise RetryExhaustedError("GitFlic request retries exhausted")

    def _raise_for_status(self, response: HttpResponse) -> None:
        if 200 <= response.status < 300:
            return
        message = self._safe_body(response)
        if response.status == 403:
            raise AuthenticationError("GitFlic authentication or package permission denied")
        if response.status == 404:
            raise DestinationError("GitFlic package destination was not found")
        if response.status == 409:
            raise DuplicateError("GitFlic package version already exists")
        raise HttpStatusError(
            f"GitFlic HTTP {response.status}: {message or 'request failed'}"
        )

    def list_package_versions(self) -> set[tuple[str, str]]:
        """Fetch all destination package versions before any upload."""
        versions: set[tuple[str, str]] = set()
        page = 0
        total_pages: int | None = None
        while total_pages is None or page < total_pages:
            url = (
                f"{self._project_package_url()}?page={page}"
                f"&size={self.config.page_size}"
            )
            response = self._request("GET", url)
            self._raise_for_status(response)
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise GitFlicError("GitFlic inventory response was invalid") from error
            if not isinstance(payload, dict):
                raise GitFlicError("GitFlic inventory response was invalid")
            page_info = payload.get("page") or {}
            if total_pages is None:
                total_pages = int(page_info.get("totalPages", 1))
            embedded = payload.get("_embedded") or {}
            entries = embedded.get("simplePackageInfoModelList") or []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name") or entry.get("packageName")
                version = entry.get("version") or entry.get("packageVersion")
                scope = entry.get("packageScope") or entry.get("scope")
                if scope and isinstance(name, str) and not name.startswith("@"):
                    name = f"@{scope}/{name}"
                if isinstance(name, str) and isinstance(version, str):
                    versions.add((name, version))
            page += 1
        return versions

    def _project_package_url(self) -> str:
        return (
            f"{self.config.base_url.rstrip('/')}/registry/project/"
            f"{quote(self.config.owner_alias, safe='')}/"
            f"{quote(self.config.project_alias, safe='')}/package"
        )

    def package_upload_url(self, artifact: PackageArtifact) -> str:
        parts = [
            self._project_package_url(),
            "npm",
            artifact.package_name,
        ]
        if artifact.package_scope:
            parts.append(artifact.package_scope)
        parts.extend([artifact.version, artifact.filename])
        return "/".join([parts[0].rstrip("/")] + [quote(part, safe="") for part in parts[1:]])

    def upload(self, artifact: PackageArtifact) -> None:
        try:
            body = artifact.source_path.read_bytes()
        except OSError as error:
            raise GitFlicError(f"could not read source archive: {artifact.source_path}") from error
        response = self._request(
            "PUT",
            self.package_upload_url(artifact),
            body=body,
            content_type="application/octet-stream",
        )
        self._raise_for_status(response)
