"""One API version: every get_service / get_type call names it; no other version is imported."""

import ast
import pathlib
import re

import pytest

import ads_mcp
from ads_mcp.api_version import API_VERSION

ROOT = pathlib.Path(ads_mcp.__file__).parent
SOURCES = sorted(p for p in ROOT.rglob("*.py"))


def _calls(path):
    tree = ast.parse(path.read_text())
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("get_type", "get_service")
    ]


@pytest.mark.parametrize(
    "path", SOURCES, ids=lambda p: str(p.relative_to(ROOT))
)
def test_every_call_names_the_version(path):
    missing = [
        c.lineno
        for c in _calls(path)
        if not any(
            k.arg == "version"
            and isinstance(k.value, ast.Name)
            and k.value.id == "API_VERSION"
            for k in c.keywords
        )
    ]
    assert (
        missing == []
    ), f"{path.name}: get_type/get_service without version=API_VERSION at {missing}"


@pytest.mark.parametrize(
    "path", SOURCES, ids=lambda p: str(p.relative_to(ROOT))
)
def test_no_other_version_is_imported(path):
    other = re.findall(r"googleads\.(v\d+)\.", path.read_text())
    assert [v for v in other if v != API_VERSION] == []


def test_the_constant():
    assert API_VERSION == "v26"
