"""`skill install|uninstall|status`, exercised against a throwaway home.

No test may touch a real `~/.agents` or `~/.claude`, so every one of them runs
against a `tmp_path` home and a fake installed tool.
"""

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
    SkillGroup,
    detected_tools,
    packaged_skill,
    refresh_skill,
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


def test_install_refuses_to_link(tool: Tool) -> None:
    """A link points into the environment, whose path carries the interpreter
    version, so a rebuild elsewhere leaves the skill silently absent."""
    result = tool.run("install", "--link")

    assert result.exit_code != 0
    assert "--link" in result.output


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
    """A link an older version installed, now dangling. Still ours to clean."""
    tool.run("install")
    manifest = tool.shared() / "SKILL.md"
    manifest.unlink()
    manifest.symlink_to(tool.source)
    tool.source.unlink()
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


def test_detected_tools_finds_antigravity_cli(tool: Tool) -> None:
    (tool.home / ".gemini" / "antigravity-cli").mkdir(parents=True)

    detected = detected_tools(tool.home)

    assert detected["Antigravity CLI"] == (
        tool.home / ".gemini" / "antigravity-cli" / "skills"
    )


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


def test_status_calls_a_matching_copy_current(tool: Tool) -> None:
    tool.run("install")

    result = tool.run("status", "--json")

    rows = json.loads(result.output)["data"]["locations"]
    shared = next(r for r in rows if r["tool"].startswith("Shared"))
    assert shared["state"] == "current"


def test_status_reports_a_copy_the_package_has_moved_past(tool: Tool) -> None:
    """Upgrading a package never refreshes a skill already on disk, so
    presence alone reported `installed` for a copy releases out of date."""
    tool.run("install")
    tool.source.write_text(MANIFEST + "a section added since\n")

    result = tool.run("status")

    assert "stale" in result.output
    assert "skill install" in result.output


def test_status_still_answers_for_an_absent_location(tool: Tool) -> None:
    result = tool.run("status", "--json")

    rows = json.loads(result.output)["data"]["locations"]
    assert all(row["state"] == "absent" for row in rows)
    assert all(row["installed"] is False for row in rows)


def test_refresh_updates_a_stale_copy(tool: Tool) -> None:
    """The whole point: an upgraded package leaves no stale skill behind."""
    tool.run("install")
    tool.source.write_text(MANIFEST + "a section added since\n")

    refreshed = refresh_skill(name=NAME, package=NAME, home=tool.home)

    assert refreshed == [tool.shared()]
    assert (tool.shared() / "SKILL.md").read_text() == tool.source.read_text()


def test_refresh_leaves_a_current_copy_alone(tool: Tool) -> None:
    tool.run("install")

    assert refresh_skill(name=NAME, package=NAME, home=tool.home) == []


def test_refresh_never_creates_a_location(tool: Tool) -> None:
    """Placing a skill somewhere new stays an explicit `skill install`, so an
    uninstall cannot be undone by the next command that happens to run."""
    (tool.home / ".claude").mkdir()

    assert refresh_skill(name=NAME, package=NAME, home=tool.home) == []
    assert not tool.shared().exists()
    assert not (tool.home / ".claude" / "skills").exists()


def test_refresh_leaves_a_foreign_directory_alone(tool: Tool) -> None:
    """The install guard has to hold for the unattended path too."""
    target = tool.home / SHARED_DIR / NAME
    manifest = _install_foreign(target)

    assert refresh_skill(name=NAME, package=NAME, home=tool.home) == []
    assert manifest.read_text() == FOREIGN


def test_refresh_covers_every_known_location(tool: Tool) -> None:
    """Including a tool since uninstalled: its copy is still one agents read."""
    (tool.home / ".claude").mkdir()
    tool.run("install")
    stale = tool.home / ".cursor" / "skills" / NAME
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text(MANIFEST)
    tool.source.write_text(MANIFEST + "a section added since\n")

    refreshed = refresh_skill(name=NAME, package=NAME, home=tool.home)

    assert stale in refreshed
    assert (tool.home / ".claude" / "skills" / NAME) in refreshed
    for target in refreshed:
        assert (target / "SKILL.md").read_text() == tool.source.read_text()


def test_refresh_survives_an_unwritable_directory(
    tool: Tool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skills directory the user has locked down is their arrangement, not
    a reason to fail the command they actually ran."""
    tool.run("install")
    tool.source.write_text(MANIFEST + "a section added since\n")

    def refuse(source, target):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(shutil, "copy2", refuse)

    assert refresh_skill(name=NAME, package=NAME, home=tool.home) == []


def test_refresh_survives_a_missing_manifest(tool: Tool) -> None:
    """Nothing to copy from is nothing to do, not a traceback."""
    tool.run("install")
    tool.source.unlink()

    assert refresh_skill(name=NAME, package=NAME, home=tool.home) == []


def test_skill_group_carries_its_bound_refresh(tool: Tool) -> None:
    """How the root group reaches a refresh it knows no name or package for."""
    tool.run("install")
    tool.source.write_text(MANIFEST + "a section added since\n")

    assert isinstance(tool.cli, SkillGroup)
    assert tool.cli.refresh() == [tool.shared()]
