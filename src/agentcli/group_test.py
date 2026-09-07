"""A bad flag must still honour `--json`, before click has parsed it."""

import json
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from agentcli import JsonAwareGroup, UsageError, emit, skill_group
from agentcli.group import NO_REFRESH_ENV
from agentcli.skill import SHARED_DIR

MANIFEST = "---\nname: faketool\ndescription: router\n---\nbody\n"


@pytest.fixture(autouse=True)
def throwaway_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every run refreshes the skills under `~`, so no test may see a real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@click.group(cls=JsonAwareGroup)
def cli() -> None:
    """Fixture tool."""


@click.command("go")
@click.option("--limit", type=click.IntRange(min=0), default=10)
@click.option("--json", "json_output", is_flag=True)
@click.option("--boom", is_flag=True)
def go(limit: int, json_output: bool, boom: bool) -> None:
    if boom:
        raise UsageError("refused on purpose")
    emit({"limit": limit}, json_output=json_output, human=lambda d: ["ok"])


cli.add_command(go)


def _run(*args: str):
    return CliRunner().invoke(cli, list(args))


def test_success_is_enveloped() -> None:
    result = _run("go", "--json")

    assert result.exit_code == 0
    assert json.loads(result.output) == {"ok": True, "data": {"limit": 10}}


def test_raised_failure_is_enveloped() -> None:
    result = _run("go", "--json", "--boom")

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "ok": False,
        "error": {"message": "refused on purpose"},
    }


def test_parse_failure_is_enveloped_though_json_never_parsed() -> None:
    """The hard case: click refuses before the subcommand sees --json."""
    result = _run("go", "--json", "--limit", "-1")

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "-1" in payload["error"]["message"]


def test_unknown_option_is_enveloped() -> None:
    result = _run("go", "--json", "--nonexistent")

    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_human_path_keeps_clicks_own_usage_block() -> None:
    """Without --json a person gets click's output, not a uniform shape."""
    result = _run("go", "--limit", "-1")

    assert result.exit_code == 1
    assert "Usage:" in result.output


def test_main_accepts_clicks_own_positional_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A caller holding a `click.Group` may pass click's own positionals."""
    group: click.Group = cli

    group.main(["go", "--json"], "tool", None, False)

    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "data": {"limit": 10},
    }


@click.group(cls=JsonAwareGroup)
def skilled() -> None:
    """A tool that ships a skill."""


skilled.add_command(go)
skilled.add_command(skill_group(name="faketool", package="agentcli"))


@pytest.mark.parametrize(
    ("args", "hinted"),
    [
        (["go", "--nope"], True),
        (["nosuchcommand"], True),
        (["go", "--limit"], False),
        (["go", "--limit", "-1"], False),
    ],
)
def test_an_unknown_name_suggests_a_stale_skill(args, hinted: bool) -> None:
    """A caller reading a skill older than the binary asks for a name the
    binary dropped. No other failure looks like that, so nothing else hints."""
    result = CliRunner().invoke(skilled, args)

    assert result.exit_code != 0
    assert ("skill install" in result.output) is hinted


def test_a_tool_without_a_skill_suggests_nothing() -> None:
    result = CliRunner().invoke(cli, ["nosuchcommand"])

    assert result.exit_code != 0
    assert "skill install" not in result.output


def _install_stale(home: Path) -> Path:
    """A skill on disk from before the manifest this package now ships."""
    target = home / SHARED_DIR / "faketool"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(MANIFEST)
    return target / "SKILL.md"


@pytest.fixture
def shipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The manifest `skilled` would install, as a newer release ships it."""
    source = tmp_path / "SKILL.md"
    source.write_text(MANIFEST + "a section added since\n")
    monkeypatch.setattr(
        "agentcli.skill.packaged_skill", lambda **kwargs: source
    )
    return source


def _as_the_command_line(
    group: click.Group, argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run `group` the way its console script does: from `sys.argv`.

    The refresh happens only when the process *is* the tool's command line,
    so a `CliRunner` -- which always hands click an argument list -- cannot
    reach it, and neither can a consumer's test suite.
    """
    monkeypatch.setattr(sys, "argv", ["tool", *argv])
    group.main(standalone_mode=False)


def test_running_any_command_refreshes_a_stale_skill(
    throwaway_home: Path, shipped: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading the package is the whole workflow: no second command."""
    installed = _install_stale(throwaway_home)

    _as_the_command_line(skilled, ["go"], monkeypatch)

    assert installed.read_text() == shipped.read_text()


def test_a_passed_argument_list_touches_no_skills(
    throwaway_home: Path, shipped: Path
) -> None:
    """A consumer's own `CliRunner` tests must not rewrite the skills under
    the developer's real home."""
    installed = _install_stale(throwaway_home)

    result = CliRunner().invoke(skilled, ["go"])

    assert result.exit_code == 0
    assert installed.read_text() == MANIFEST


def test_the_skill_command_reports_drift_rather_than_hiding_it(
    throwaway_home: Path,
    shipped: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`skill status` describing a copy it had just silently repaired would
    have nothing left to report."""
    installed = _install_stale(throwaway_home)

    _as_the_command_line(skilled, ["skill", "status"], monkeypatch)

    assert "stale" in capsys.readouterr().out
    assert installed.read_text() == MANIFEST


def test_refresh_can_be_switched_off(
    throwaway_home: Path, shipped: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(NO_REFRESH_ENV, "1")
    installed = _install_stale(throwaway_home)

    _as_the_command_line(skilled, ["go"], monkeypatch)

    assert installed.read_text() == MANIFEST


def test_a_tool_without_a_skill_group_still_runs(
    throwaway_home: Path,
    shipped: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The refresh is opportunistic; nothing to refresh is not an error."""
    _as_the_command_line(cli, ["go", "--json"], monkeypatch)

    assert json.loads(capsys.readouterr().out)["ok"] is True
