"""A bad flag must still honour `--json`, before click has parsed it."""

import json

import click
import pytest
from click.testing import CliRunner

from agentcli import JsonAwareGroup, UsageError, emit, skill_group


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
