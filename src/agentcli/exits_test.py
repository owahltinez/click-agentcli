"""Exit codes are the contract an agent reads instead of prose."""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from agentcli.exits import (
    AssertionFailure,
    RemoteError,
    StrictFailure,
    UsageError,
)

DOCUMENTED = [
    (UsageError, 1),
    (RemoteError, 2),
    (AssertionFailure, 3),
    (StrictFailure, 4),
]


@pytest.mark.parametrize(("error", "code"), DOCUMENTED)
def test_class_declares_documented_code(error, code) -> None:
    assert error.exit_code == code


@pytest.mark.parametrize(("error", "code"), DOCUMENTED)
def test_raising_exits_with_documented_code(error, code) -> None:
    """The number a caller sees, not just the one the class declares."""

    @click.command()
    def cmd() -> None:
        raise error("no")

    result = CliRunner().invoke(cmd, [])

    assert result.exit_code == code


def test_success_is_zero() -> None:
    @click.command()
    def cmd() -> None:
        click.echo("ok")

    assert CliRunner().invoke(cmd, []).exit_code == 0


def test_click_parse_failures_are_usage_errors_not_remote_ones() -> None:
    """A mistyped flag must not look like a network outage to an agent.

    Click's own `UsageError` defaults to exit 2, the code this project reserves
    for a remote failure. Importing agentcli has to correct that, because a
    tool cannot opt into the convention it forgot to apply.
    """

    @click.command()
    @click.option("--limit", type=click.IntRange(min=0), default=10)
    def cli(limit: int) -> None:
        click.echo(limit)

    runner = CliRunner()
    assert runner.invoke(cli, ["--limit", "-1"]).exit_code == 1
    assert runner.invoke(cli, ["--nonexistent"]).exit_code == 1
    assert runner.invoke(cli, []).exit_code == 0
