"""The manifest and the audited dependency group must name the same requirements."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_manifest_requirements_match_the_runtime_group() -> None:
    """A requirement changed in one place and not the other would escape the audit."""
    manifest = json.loads((ROOT / "custom_components/snapdog/manifest.json").read_text())
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert manifest["requirements"] == pyproject["dependency-groups"]["runtime"]
