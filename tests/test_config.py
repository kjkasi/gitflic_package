import json
from pathlib import Path

import pytest

from gitflic_package.config import (
    GitFlicConfig,
    load_config,
    resolve_token,
)


def write_config(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def valid_config(source_dir: Path) -> dict:
    return {
        "source_dir": str(source_dir),
        "gitflic": {
            "base_url": "https://registry.gitflic.ru/",
            "owner_alias": "team",
            "project_alias": "npm-cache",
            "token_env": "GITFLIC_TOKEN",
            "timeout_seconds": 60,
            "page_size": 100,
        },
    }


def test_load_config_applies_defaults_and_normalizes_base_url(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, valid_config(tmp_path)))

    assert config.source_dir == tmp_path
    assert config.gitflic.base_url == "https://registry.gitflic.ru"
    assert config.gitflic.owner_alias == "team"
    assert config.gitflic.project_alias == "npm-cache"
    assert config.gitflic.token_env == "GITFLIC_TOKEN"
    assert config.gitflic.timeout_seconds == 60.0
    assert config.gitflic.page_size == 100
    assert config.rate_limit.min_interval_seconds == 10.0
    assert config.rate_limit.max_retries == 5


def test_load_config_reads_explicit_rate_limit(tmp_path: Path) -> None:
    data = valid_config(tmp_path)
    data["rate_limit"] = {"min_interval_seconds": 0.25, "max_retries": 2}

    config = load_config(write_config(tmp_path, data))

    assert config.rate_limit.min_interval_seconds == 0.25
    assert config.rate_limit.max_retries == 2


def test_load_config_rejects_missing_source_dir(tmp_path: Path) -> None:
    data = valid_config(tmp_path / "\\\\server\\verdaccio\\storage")

    with pytest.raises(ValueError, match="source_dir") as error:
        load_config(write_config(tmp_path, data))

    assert "GITFLIC_TOKEN" not in str(error.value)


def test_load_config_rejects_invalid_positive_values(tmp_path: Path) -> None:
    for field, value in (("timeout_seconds", 0), ("page_size", 0)):
        data = valid_config(tmp_path)
        data["gitflic"][field] = value
        with pytest.raises(ValueError, match=field):
            load_config(write_config(tmp_path, data))

    data = valid_config(tmp_path)
    data["rate_limit"] = {"min_interval_seconds": -1, "max_retries": -1}
    with pytest.raises(ValueError, match="min_interval_seconds"):
        load_config(write_config(tmp_path, data))


def test_resolve_token_reads_named_environment_variable_and_rejects_missing_token() -> None:
    config = GitFlicConfig(
        base_url="https://registry.gitflic.ru",
        owner_alias="team",
        project_alias="npm-cache",
        token_env="CUSTOM_TOKEN",
        timeout_seconds=60.0,
        page_size=100,
    )

    assert resolve_token(config, {"CUSTOM_TOKEN": "SECRET"}) == "SECRET"

    with pytest.raises(ValueError, match="CUSTOM_TOKEN") as error:
        resolve_token(config, {})
    assert "SECRET" not in str(error.value)
