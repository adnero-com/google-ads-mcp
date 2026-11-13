"""plans.py: where the plan key comes from; plans from one key never verify under another."""

import pytest
from fastmcp.exceptions import ToolError

from ads_mcp import plans
from ads_mcp.core import plan as core

pytestmark = pytest.mark.real_plan_key


@pytest.fixture(autouse=True)
def fresh():
    plans.reset()
    yield
    plans.reset()


@pytest.mark.parametrize(
    "env, stable",
    [
        ({"GOOGLE_ADS_MCP_PLAN_SECRET": "p" * 32}, True),
        ({"GOOGLE_ADS_MCP_SHARED_SECRET": "g" * 32}, True),
        (
            {
                "GOOGLE_ADS_MCP_PLAN_SECRET": "short",
                "GOOGLE_ADS_MCP_SHARED_SECRET": "g" * 32,
            },
            True,
        ),
        ({}, None),  # no key at all: writes are refused
    ],
)
def test_key_sources(monkeypatch, env, stable):
    for k in ("GOOGLE_ADS_MCP_PLAN_SECRET", "GOOGLE_ADS_MCP_SHARED_SECRET"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    digest = core.digest_of("t", "1", {"a": 1})
    if stable is None:
        with pytest.raises(ToolError, match="writes are off"):
            plans.issue(digest)
        assert "writes are off" in plans.problem(
            "p1.1." + "0" * 16 + "." + "0" * 64, digest
        )
        return
    issued = plans.issue(digest)
    assert plans.problem(issued, digest) is None
    plans.reset()
    assert (
        plans.problem(issued, digest) is None
    )  # survives a restart: the key is stable


def test_derived_key_is_not_the_gateway_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_MCP_PLAN_SECRET", raising=False)
    monkeypatch.setenv("GOOGLE_ADS_MCP_SHARED_SECRET", "g" * 32)
    assert plans._secret() != b"g" * 32


def test_problem_messages(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_MCP_PLAN_SECRET", "p" * 32)
    digest = core.digest_of("t", "1", {})
    assert "required" in plans.problem(None, digest)
    assert "not issued" in plans.problem("deadbeef", digest)
    monkeypatch.setattr(plans, "now", lambda: 0)
    old = plans.issue(digest)
    monkeypatch.setattr(plans, "now", lambda: 10_000)
    assert "expired" in plans.problem(old, digest)


def test_a_plan_applies_once(monkeypatch):
    monkeypatch.setenv("GOOGLE_ADS_MCP_PLAN_SECRET", "p" * 32)
    digest = core.digest_of("t", "1", {})
    issued = plans.issue(digest)
    assert plans.consume(issued, digest) is None
    assert "already applied" in plans.consume(issued, digest)
    assert "already applied" in plans.problem(issued, digest)
    assert (
        plans.consume(plans.issue(digest), digest) is None
    )  # a new preview is a new plan
