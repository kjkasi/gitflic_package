from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_design_spec_uses_api_access_token_consistently() -> None:
    spec = (ROOT / "docs/superpowers/specs/2026-10-04-gitflic-npm-migration-design.md").read_text(
        encoding="utf-8"
    )

    assert "transport token" not in spec.lower()
    assert "API access token" in spec


def test_readme_documents_implicit_default_migration() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "existing configuration" in readme.lower()
    assert "GITFLIC_TOKEN" in readme
    assert "GITFLIC_API_TOKEN" in readme
    assert "token_env" in readme
