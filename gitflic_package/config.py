"""Configuration loading and token resolution for the migration CLI."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class GitFlicConfig:
    base_url: str
    owner_alias: str
    project_alias: str
    token_env: str
    timeout_seconds: float
    page_size: int


@dataclass(frozen=True)
class RateLimitConfig:
    min_interval_seconds: float = 10.0
    max_retries: int = 5


@dataclass(frozen=True)
class AppConfig:
    source_dir: Path
    gitflic: GitFlicConfig
    rate_limit: RateLimitConfig


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be positive")
    try:
        converted = float(value)
    except OverflowError as error:
        raise ValueError(f"{field} must be positive") from error
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError(f"{field} must be positive")
    return converted


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nonnegative_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be non-negative")
    try:
        converted = float(value)
    except OverflowError as error:
        raise ValueError(f"{field} must be non-negative") from error
    if not math.isfinite(converted) or converted < 0:
        raise ValueError(f"{field} must be non-negative")
    return converted


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def load_config(path: Path) -> AppConfig:
    """Load and validate an application configuration JSON file."""
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read configuration: {error}") from error

    if not isinstance(raw, dict):
        raise ValueError("configuration must be a JSON object")

    source_value = raw.get("source_dir")
    source_dir = Path(_required_string(source_value, "source_dir"))
    if not source_dir.is_dir():
        raise ValueError(f"source_dir is not an existing directory: {source_dir}")

    gitflic = raw.get("gitflic")
    if not isinstance(gitflic, dict):
        raise ValueError("gitflic must be an object")

    base_url = _required_string(
        gitflic.get("base_url", "https://api.gitflic.ru"), "base_url"
    ).rstrip("/")
    try:
        parsed_base_url = urlsplit(base_url)
        hostname = parsed_base_url.hostname
        port = parsed_base_url.port
    except ValueError as error:
        raise ValueError("base_url must be an HTTPS URL") from error
    if (
        parsed_base_url.scheme.lower() != "https"
        or not parsed_base_url.netloc
        or not hostname
        or parsed_base_url.username is not None
        or parsed_base_url.password is not None
        or "\\" in parsed_base_url.netloc
        or "?" in base_url
        or "#" in base_url
        or parsed_base_url.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("base_url must be an HTTPS URL")

    gitflic_config = GitFlicConfig(
        base_url=base_url,
        owner_alias=_required_string(gitflic.get("owner_alias"), "owner_alias"),
        project_alias=_required_string(gitflic.get("project_alias"), "project_alias"),
        token_env=_required_string(
            gitflic.get("token_env", "GITFLIC_API_TOKEN"), "token_env"
        ),
        timeout_seconds=_positive_float(
            gitflic.get("timeout_seconds", 60), "timeout_seconds"
        ),
        page_size=_positive_int(gitflic.get("page_size", 100), "page_size"),
    )

    rate_limit = raw.get("rate_limit", {})
    if not isinstance(rate_limit, dict):
        raise ValueError("rate_limit must be an object")
    rate_config = RateLimitConfig(
        min_interval_seconds=_nonnegative_float(
            rate_limit.get("min_interval_seconds", 10.0),
            "min_interval_seconds",
        ),
        max_retries=_nonnegative_int(
            rate_limit.get("max_retries", 5), "max_retries"
        ),
    )
    return AppConfig(
        source_dir=source_dir,
        gitflic=gitflic_config,
        rate_limit=rate_config,
    )


def resolve_token(
    config: GitFlicConfig, environ: Mapping[str, str] | None = None
) -> str:
    """Resolve the configured API access token without storing it in config."""
    environment = os.environ if environ is None else environ
    token = environment.get(config.token_env)
    if token is None or not token.strip():
        raise ValueError(f"environment variable {config.token_env} is missing or empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in token):
        raise ValueError(f"environment variable {config.token_env} contains invalid token characters")
    return token
