"""`skill install|uninstall|status`, exercised against a throwaway home.

No test may touch a real `~/.agents` or `~/.claude`, so every one of them runs
against a `tmp_path` home and a fake installed tool.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from agentcli.skill import (
    SHARED_DIR,
    detected_tools,
    packaged_skill,
    skill_group,
)

NAME = "faketool"
MANIFEST = f"---\nname: {NAME}\ndescription: router\n---\nbody\n"
FOREIGN = "---\nname: someone-elses-skill\n---\nimportant work\n"


@dataclass
class Tool:
    """A fake installed tool: importable package, checkout SKILL.md, home."""

    runner: CliRunner
    cli: click.Group
    home: Path
    source: Path
    root: Path

    def run(self, *args: str):
        return self.runner.invoke(self.cli, list(args))

    def shared(self) -> Path:
        return self.home / SHARED_DIR / NAME


@pytest.fixture
def tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Tool]:
    """Build the fake tool and point `Path.home` at a throwaway directory."""
    root = tmp_path / "checkout"
    package = root / "src" / NAME
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    source = root / "SKILL.md"
    source.write_text(MANIFEST)

    # The package has to be genuinely importable: `packaged_skill` resolves it
    # through `importlib.resources`, which is the part worth testing.
    monkeypatch.syspath_prepend(str(root / "src"))
    sys.modules.pop(NAME, None)
    importlib.invalidate_caches()

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)

    yield Tool(
        runner=CliRunner(),
        cli=skill_group(name=NAME, package=NAME),
        home=home,
        source=source,
        root=root,
    )

    sys.modules.pop(NAME, None)


def _install_foreign(target: Path) -> Path:
    """Someone else's skill directory, which we must never touch."""
    target.mkdir(parents=True)
    manifest = target / "SKILL.md"
    manifest.write_text(FOREIGN)
    return manifest


def test_packaged_skill_prefers_the_wheel_copy(tool: Tool) -> None:
    packaged = tool.root / "src" / NAME / "skills" / NAME / "SKILL.md"
    packaged.parent.mkdir(parents=True)
    packaged.write_text(MANIFEST)

    assert packaged_skill(name=NAME, package=NAME) == packaged


def test_packaged_skill_falls_back_to_the_checkout(tool: Tool) -> None:
    assert packaged_skill(name=NAME, package=NAME) == tool.source


def test_packaged_skill_reports_where_it_looked(tool: Tool) -> None:
    tool.source.unlink()

    with pytest.raises(Exception, match="SKILL.md not found"):
        packaged_skill(name=NAME, package=NAME)


def test_install_into_a_fresh_home(tool: Tool) -> None:
    result = tool.run("install")

    assert result.exit_code == 0
    assert (tool.shared() / "SKILL.md").read_text() == MANIFEST
    # Copy, not link, so `uv cache prune` cannot take the skill with it.
    assert not (tool.shared() / "SKILL.md").is_symlink()


def test_install_refuses_a_foreign_directory(tool: Tool) -> None:
    skills = tool.home / "elsewhere"
    manifest = _install_foreign(skills / NAME)

    result = tool.run("install", "--to", str(skills))

    assert result.exit_code == 1
    assert "does not contain the faketool skill" in result.output
    assert manifest.read_text() == FOREIGN


def test_install_refreshes_our_own_copy(tool: Tool) -> None:
    """Re-running install is how a stale skill gets updated, so it must work.

    The packaged manifest is the source of truth, which makes replacing our
    own copy idempotent rather than destructive.
    """
    tool.run("install")
    (tool.shared() / "SKILL.md").write_text("stale\nname: faketool\n")

    result = tool.run("install")

    assert result.exit_code == 0
    assert (tool.shared() / "SKILL.md").read_text() == MANIFEST


def test_install_link_makes_a_symlink(tool: Tool) -> None:
    result = tool.run("install", "--link")

    assert result.exit_code == 0
    manifest = tool.shared() / "SKILL.md"
    assert manifest.is_symlink()
    assert manifest.resolve() == tool.source.resolve()


def test_install_covers_detected_tools_without_a_flag(tool: Tool) -> None:
    """Installing a skill everywhere it is wanted is the whole job."""
    (tool.home / ".claude").mkdir()
    (tool.home / ".gemini").mkdir()

    result = tool.run("install")

    assert result.exit_code == 0
    for relative in (
        SHARED_DIR,
        Path(".claude") / "skills",
        Path(".gemini") / "skills",
        Path(".gemini") / "config" / "skills",
    ):
        assert (tool.home / relative / NAME / "SKILL.md").is_file()
    # An undetected tool is never created.
    assert not (tool.home / ".cursor").exists()


def test_install_all_dedupes_an_overlapping_to(tool: Tool) -> None:
    """`--to` naming a swept directory must not refuse itself."""
    (tool.home / ".claude").mkdir()
    skills = tool.home / ".claude" / "skills"

    result = tool.run("install", "--to", str(skills))

    assert result.exit_code == 0
    assert result.output.count(str(skills / NAME)) == 1


def test_install_dry_run_touches_nothing(tool: Tool) -> None:
    result = tool.run("install", "--dry-run")

    assert result.exit_code == 0
    assert "would install" in result.output
    assert not (tool.home / ".agents").exists()


def test_install_dry_run_predicts_a_foreign_refusal(tool: Tool) -> None:
    skills = tool.home / "elsewhere"
    _install_foreign(skills / NAME)

    result = tool.run("install", "--to", str(skills), "--dry-run")

    assert result.exit_code == 0
    assert "would REFUSE" in result.output
    assert "does not contain the faketool skill" in result.output


def test_install_dry_run_predicts_a_refresh(tool: Tool) -> None:
    """Re-installing over our own copy is a plain install, not a refusal."""
    tool.run("install")

    result = tool.run("install", "--dry-run")

    assert result.exit_code == 0
    assert "would install" in result.output
    assert "would REFUSE" not in result.output


def test_uninstall_removes_our_own(tool: Tool) -> None:
    tool.run("install")

    result = tool.run("uninstall")

    assert result.exit_code == 0
    assert not tool.shared().exists()


def test_uninstall_refuses_a_foreign_directory(tool: Tool) -> None:
    skills = tool.home / "elsewhere"
    manifest = _install_foreign(skills / NAME)

    result = tool.run("uninstall", "--to", str(skills))

    assert result.exit_code == 1
    assert "refusing to delete it" in result.output
    assert manifest.read_text() == FOREIGN


def test_uninstall_removes_a_broken_symlink(tool: Tool) -> None:
    """A `--link` install survived `uv cache prune`; clean it up anyway."""
    tool.run("install", "--link")
    tool.source.unlink()
    manifest = tool.shared() / "SKILL.md"
    assert manifest.is_symlink() and not manifest.exists()

    result = tool.run("uninstall")

    assert result.exit_code == 0
    assert not tool.shared().exists()


def test_uninstall_sweeps_uninstalled_tools(tool: Tool) -> None:
    """A removed tool can leave a skill behind; that is what needs sweeping."""
    stale = tool.home / ".cursor" / "skills" / NAME
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text(MANIFEST)

    result = tool.run("uninstall")

    assert result.exit_code == 0
    assert not stale.exists()


def test_uninstall_dry_run_touches_nothing(tool: Tool) -> None:
    tool.run("install")

    result = tool.run("uninstall", "--dry-run")

    assert result.exit_code == 0
    assert "would remove" in result.output
    assert (tool.shared() / "SKILL.md").is_file()


def test_uninstall_reports_an_empty_sweep(tool: Tool) -> None:
    result = tool.run("uninstall")

    assert result.exit_code == 0
    assert result.output.strip() == "nothing to remove"


def test_detected_tools_needs_the_marker(tool: Tool) -> None:
    assert detected_tools(tool.home) == {}

    (tool.home / ".gemini").mkdir()

    assert set(detected_tools(tool.home)) == {"Gemini CLI", "Antigravity"}


def test_status_json_is_one_object(tool: Tool) -> None:
    tool.run("install")

    result = tool.run("status", "--json")

    assert result.exit_code == 0
    assert len(result.output.strip().splitlines()) == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    payload = envelope["data"]
    assert payload["skill"] == NAME
    installed = [r for r in payload["locations"] if r["installed"]]
    assert [r["path"] for r in installed] == [str(tool.shared())]


def test_status_human_lists_every_location(tool: Tool) -> None:
    result = tool.run("status")

    assert result.exit_code == 0
    assert "Claude Code" in result.output
    assert "installed" not in result.output


def test_install_link_failure_falls_back_to_copying(
    tool: Tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows without Developer Mode: a copy beats a traceback."""

    def refuse(self, target, **kwargs):
        raise OSError("symlinks need a privilege you do not have")

    monkeypatch.setattr(Path, "symlink_to", refuse)

    result = tool.run("install", "--link")

    assert result.exit_code == 0
    manifest = tool.shared() / "SKILL.md"
    assert not manifest.is_symlink()
    assert manifest.read_text() == MANIFEST


def test_install_wraps_oserror(
    tool: Tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk is an error message, not a stack trace."""

    def refuse(source, target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(shutil, "copy2", refuse)

    result = tool.run("install")

    assert result.exit_code == 1
    assert "could not install into" in result.output
    assert "No space left on device" in result.output


def test_uninstall_wraps_oserror(
    tool: Tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool.run("install")

    def refuse(path):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(shutil, "rmtree", refuse)

    result = tool.run("uninstall")

    assert result.exit_code == 1
    assert "could not remove" in result.output
    assert "Permission denied" in result.output


def test_install_to_stays_scoped_to_one_directory(tool: Tool) -> None:
    """`--to` is how you opt out of the sweep."""
    (tool.home / ".claude").mkdir()
    only = tool.home / "only"

    result = tool.run("install", "--to", str(only))

    assert result.exit_code == 0
    assert (only / NAME / "SKILL.md").is_file()
    assert not (tool.home / ".claude" / "skills").exists()
    assert not tool.shared().exists()


def test_uninstall_matches_what_install_wrote(tool: Tool) -> None:
    """An asymmetric default would strand copies install had made."""
    (tool.home / ".claude").mkdir()
    (tool.home / ".gemini").mkdir()
    tool.run("install")

    result = tool.run("uninstall")

    assert result.exit_code == 0
    for relative in (
        SHARED_DIR,
        Path(".claude") / "skills",
        Path(".gemini") / "skills",
        Path(".gemini") / "config" / "skills",
    ):
        assert not (tool.home / relative / NAME).exists()
