"""One JSON object on stdout, or human text. Never both."""

import json

import click
import pytest
from click.testing import CliRunner

from agentcli.output import emit, emit_error, json_option, limit_option


def test_json_emits_exactly_one_object(capsys) -> None:
    emit(
        {"items": [1, 2], "count": 2},
        json_output=True,
        human=lambda payload: ["unused"],
    )

    out = capsys.readouterr()
    assert out.err == ""
    # Exactly one line, and that line is the payload inside the envelope.
    assert json.loads(out.out.strip()) == {
        "ok": True,
        "data": {"items": [1, 2], "count": 2},
    }
    assert len(out.out.strip().splitlines()) == 1


def test_human_path_never_emits_json(capsys) -> None:
    emit({"n": 1}, json_output=False, human=lambda p: [f"n is {p['n']}"])

    out = capsys.readouterr()
    assert out.out == "n is 1\n"
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.out)


def test_emit_error_json_goes_to_stdout(capsys) -> None:
    """`--json` promises stdout is parseable, failures included."""
    emit_error("boom", json_output=True)

    out = capsys.readouterr()
    assert out.err == ""
    assert json.loads(out.out) == {
        "ok": False,
        "error": {"message": "boom"},
    }


def test_emit_error_human_goes_to_stderr(capsys) -> None:
    emit_error("boom", json_output=False)

    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "boom\n"


def test_json_option_defaults_to_human() -> None:
    @click.command()
    @json_option
    def cmd(json_output: bool) -> None:
        click.echo(str(json_output))

    assert CliRunner().invoke(cmd, []).output == "False\n"
    assert CliRunner().invoke(cmd, ["--json"]).output == "True\n"


def test_limit_option_rejects_negative() -> None:
    """A negative limit is a bad flag, not something to clamp silently."""

    @click.command()
    @limit_option()
    def cmd(limit: int) -> None:
        click.echo(str(limit))

    result = CliRunner().invoke(cmd, ["--limit", "-1"])

    assert result.exit_code != 0
    assert "--limit" in result.output


def test_limit_option_default_and_zero() -> None:
    @click.command()
    @limit_option(5)
    def cmd(limit: int) -> None:
        click.echo(str(limit))

    assert CliRunner().invoke(cmd, []).output == "5\n"
    assert CliRunner().invoke(cmd, ["--limit", "0"]).output == "0\n"
