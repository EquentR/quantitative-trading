from __future__ import annotations

import tomllib
from pathlib import Path


def load_pyproject() -> dict:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def test_dev_dependency_group_and_pip_extra_are_mirrored() -> None:
    pyproject = load_pyproject()
    dependency_groups = pyproject.get("dependency-groups", {})
    optional_dependencies = pyproject.get("project", {}).get("optional-dependencies", {})

    assert "dev" in dependency_groups
    assert "dev" in optional_dependencies
    assert dependency_groups["dev"] == optional_dependencies["dev"]
