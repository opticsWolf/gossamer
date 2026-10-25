"""Skill drift tests: the bundled agent skill cannot rot.

``gossamer/skills/gossamer/SKILL.md`` is the source of truth agents read.
It lives inside the package (not at the repo root) so maturin copies it
into the wheel; these tests read it through ``importlib.resources``, the
same path an installed release uses.

Every tool name the skill names must exist in the tool registry; every
CLI subcommand the skill teaches must exist in the argparse parser. A doc
edit naming a phantom tool fails here, not in production.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

import pytest

from gossamer.agent_tools import TOOL_REGISTRY
from gossamer.cli import build_parser
from gossamer.skills import (
    SkillNotFound,
    default_destinations,
    install,
    iter_skills,
    skill_description,
    skill_names,
    skill_text,
)

SKILLS = Path(__file__).parent.parent / "gossamer" / "skills"


def _skill() -> str:
    return skill_text("gossamer")


def _bullet(text: str, header: str) -> str:
    """The full bullet starting with ``- **header**`` (continuation lines in)."""
    match = re.search(
        rf"- \*\*{re.escape(header)}\*\*(.*?)(?=\n- |\n## |\Z)", text, re.DOTALL
    )
    assert match, f"SKILL.md has no '{header}' bullet"
    return match.group(0)


# ────────────────────────────────────────────────────────────────
# Bundling (packaging truth)
# ────────────────────────────────────────────────────────────────


def test_skill_ships_in_the_package_tree():
    assert (SKILLS / "gossamer" / "SKILL.md").is_file()


def test_skill_names_and_frontmatter():
    assert skill_names() == ("gossamer",)
    text = _skill()
    assert text.startswith("---\n")
    assert "name: gossamer" in text
    # skill_description raises unless a non-empty description exists and
    # the frontmatter name matches the directory.
    assert skill_description("gossamer")
    assert [name for name, _ in iter_skills()] == ["gossamer"]


def test_skill_text_read_via_importlib_resources():
    text = _skill()
    assert "# Gossamer" in text
    assert "literature collection workflow" in text.lower()


# ────────────────────────────────────────────────────────────────
# Drift: tool surface
# ────────────────────────────────────────────────────────────────


def test_skill_names_only_registered_tools():
    text = _skill()
    named = {
        span
        for span in re.findall(r"`([^`]+)`", _bullet(text, "MCP tools"))
        if re.fullmatch(r"[a-z_]+", span)
    }
    registered = {spec.name for spec in TOOL_REGISTRY}
    assert named, "no tool names extracted from the MCP bullet"
    assert named <= registered, f"phantom tools: {sorted(named - registered)}"
    # The skill claims to list *all* MCP tools 1:1 — keep that honest.
    assert named == registered, (
        f"registry/skill drift: missing={sorted(registered - named)}, "
        f"phantom={sorted(named - registered)}"
    )


# ────────────────────────────────────────────────────────────────
# Drift: CLI surface
# ────────────────────────────────────────────────────────────────


def _subcommand_choices() -> set[str]:
    parser = build_parser()
    sub = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    return set(sub.choices)


def test_skill_names_only_existing_cli_commands():
    text = _skill()
    commands = set()
    for span in re.findall(r"`([^`]+)`", _bullet(text, "CLI")):
        match = re.match(r"([a-z][a-z0-9-]*)", span)
        if match:
            commands.add(match.group(1))
    expected = {
        "search", "research", "inspect", "batch", "download", "locate-pdf",
        "extract", "check", "discover", "crawl", "cache", "cite", "categories",
        "skills",
    }
    assert commands == expected, (
        f"CLI bullet drift: unexpected={sorted(commands - expected)}, "
        f"missing={sorted(expected - commands)}"
    )
    choices = _subcommand_choices()
    phantom = commands - choices
    assert not phantom, f"SKILL.md teaches phantom CLI commands: {sorted(phantom)}"


# ────────────────────────────────────────────────────────────────
# Install to harness discovery directories
# ────────────────────────────────────────────────────────────────


def test_install_is_non_destructive_and_force_updates(tmp_path):
    first = install(tmp_path)
    assert [r.status for r in first] == ["installed"]
    copied = tmp_path / "gossamer" / "SKILL.md"
    assert copied.is_file() and copied.read_text(encoding="utf-8") == _skill()

    again = install(tmp_path)
    assert [r.status for r in again] == ["skipped"]

    forced = install(tmp_path, force=True)
    assert [r.status for r in forced] == ["updated"]


def test_install_rejects_unknown_names(tmp_path):
    with pytest.raises(SkillNotFound, match="unknown skill"):
        install(tmp_path, names=["not-a-skill"])


def test_default_destinations(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.chdir(tmp_path)
    personal = default_destinations()
    assert personal == (
        tmp_path / "home" / ".claude" / "skills",
        tmp_path / "home" / ".config" / "opencode" / "skills",
    )
    project = default_destinations(project=True)
    assert project == (
        tmp_path / ".claude" / "skills",
        tmp_path / ".opencode" / "skills",
    )


# ────────────────────────────────────────────────────────────────
# CLI surface (gossamer skills list/show/install)
# ────────────────────────────────────────────────────────────────


def test_cli_skills_list(capsys):
    from gossamer.cli import main

    assert main(["skills", "list"]) == 0
    out = capsys.readouterr().out
    assert "gossamer" in out
    assert "Install with `gossamer skills install`." in out


def test_cli_skills_show(capsys):
    from gossamer.cli import main

    assert main(["skills", "show", "gossamer"]) == 0
    out = capsys.readouterr().out
    assert out == _skill()

    assert main(["skills", "show", "nope"]) == 1
    assert "unknown skill" in capsys.readouterr().err


def test_cli_skills_install(tmp_path, capsys):
    from gossamer.cli import main

    dest = tmp_path / "skills-root"
    assert main(["skills", "install", "--dest", str(dest)]) == 0
    assert "installed" in capsys.readouterr().out
    assert (dest / "gossamer" / "SKILL.md").is_file()

    assert main(["skills", "install", "--dest", str(dest)]) == 0
    assert "already present" in capsys.readouterr().out

    assert main(["skills", "install", "--dest", str(tmp_path / "d"),
                 "--project"]) == 1
    assert "mutually exclusive" in capsys.readouterr().err


# ────────────────────────────────────────────────────────────────
# MCP resource surface
# ────────────────────────────────────────────────────────────────


def test_mcp_server_exposes_the_skill_resource():
    mcp_server = pytest.importorskip("gossamer.mcp_server")
    server = mcp_server.build_server()
    listed = asyncio.run(server.list_resources())
    assert {str(r.uri) for r in listed} == {"skills://gossamer"}
    assert {r.name for r in listed} == {"gossamer"}
    contents = asyncio.run(server.read_resource("skills://gossamer"))
    assert len(contents) == 1
    assert contents[0].content == _skill()


def test_mcp_instructions_teach_the_skill_surface():
    mcp_server = pytest.importorskip("gossamer.mcp_server")
    instructions = mcp_server.INSTRUCTIONS
    assert "skills://gossamer" in instructions
    assert "gossamer skills install" in instructions
    assert "gossamer skills show" in instructions
