"""Every translation carries the keys and placeholders of the English one."""

import json
import re
from pathlib import Path

import pytest

DIRECTORY = Path(__file__).parent.parent / "custom_components/snapdog/translations"
ENGLISH = json.loads((DIRECTORY / "en.json").read_text())
OTHERS = sorted(path for path in DIRECTORY.glob("*.json") if path.stem != "en")


def flatten(tree: dict, prefix: str = "") -> dict[str, str]:
    """Map the dotted path of every text to the text."""
    flat: dict[str, str] = {}
    for key, value in tree.items():
        if isinstance(value, dict):
            flat.update(flatten(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def test_there_are_translations() -> None:
    """A moved directory would make the checks below pass on nothing."""
    assert len(OTHERS) >= 6


@pytest.mark.parametrize("path", OTHERS, ids=lambda path: path.stem)
def test_translation_matches_english(path: Path) -> None:
    """Same keys, same placeholders, so that no text is missing or fails to format."""
    english = flatten(ENGLISH)
    translated = flatten(json.loads(path.read_text()))
    assert translated.keys() == english.keys()
    for key, text in english.items():
        assert set(re.findall(r"{\w+}", translated[key])) == set(re.findall(r"{\w+}", text)), key
