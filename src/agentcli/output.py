"""One JSON object, or a human table. Never both, never anything else.

The whole point of `--json` is that a composing agent can read stdout with a
parser instead of a regular expression, so the machine path emits exactly one
object and the human path is free to be pretty.

Success and failure are deliberately symmetric:

    {"ok": true,  "data":  {...}}
    {"ok": false, "error": {"message": "..."}}

so a consumer branches on one key it can always rely on, in every tool and
every command. The alternative -- a bare payload on success and a differently
shaped object on failure -- makes every caller special-case both, which is how
four tools end up with four contracts.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

import click


def dumps(payload: Any) -> str:
    """Compact, stable JSON. Sorted keys would fight the record key order."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def emit(
    data: dict[str, Any],
    *,
    json_output: bool,
    human: Callable[[dict[str, Any]], Iterable[str]],
) -> None:
    """Write one successful result, in whichever format was requested.

    The envelope is added here so no command can forget it. `human` receives
    the unwrapped data, because the wrapper exists for parsers, not people.
    """
    if json_output:
        click.echo(dumps({"ok": True, "data": data}))
        return

    for line in human(data):
        click.echo(line)


def emit_error(message: str, *, json_output: bool) -> None:
    """Report a failure on stdout when JSON was requested, stderr otherwise.

    Requesting JSON is a promise that stdout is parseable, and a caller that
    has to merge two streams to find the error does not have that.
    """
    if json_output:
        click.echo(dumps({"ok": False, "error": {"message": message}}))
    else:
        click.echo(message, err=True)


def json_option(f: Callable[..., Any]) -> Callable[..., Any]:
    """The shared `--json` flag, identical in every tool."""
    return click.option(
        "--json",
        "json_output",
        is_flag=True,
        help="Emit exactly one JSON object on stdout.",
    )(f)


def limit_option(default: int = 10) -> Callable[..., Any]:
    """The shared `--limit` flag. Rejects negatives rather than clamping."""

    def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
        return click.option(
            "--limit",
            type=click.IntRange(min=0),
            default=default,
            show_default=True,
            help="Maximum results to return.",
        )(f)

    return decorator
