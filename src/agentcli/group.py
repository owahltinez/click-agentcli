"""A command group that keeps the `--json` promise even when it fails.

Requesting JSON is a promise that stdout is parseable, and that has to hold for
failures too. The hard case is a bad flag: click raises before any subcommand
has parsed `--json`, so at that point nothing in the parsed context knows JSON
was wanted. The raw argument list is the only place it is known that early.

This lives here rather than in each tool because every tool has the same
problem, and a tool that solves it locally is a tool the next one forgets to
copy.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any, Literal, NoReturn, overload

import click

from agentcli.output import emit_error


class JsonAwareGroup(click.Group):
    """Routes `click.ClickException` to the JSON error shape when asked."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._json_requested = False

    # The overloads mirror `click.Group.main` exactly, so a caller holding a
    # `click.Group` can call this the same way, positionals included.
    @overload
    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: Literal[True] = True,
        **extra: Any,
    ) -> NoReturn: ...

    @overload
    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = ...,
        **extra: Any,
    ) -> Any: ...

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        **extra: Any,
    ) -> Any:
        arguments = list(sys.argv[1:] if args is None else args)
        self._json_requested = "--json" in arguments

        return super().main(
            arguments,
            prog_name,
            complete_var,
            standalone_mode,
            **extra,
        )

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except click.ClickException as exc:
            # The human path stays click's own, which prints the usage block
            # too: worth more to a person than a uniform shape.
            if not self._json_requested:
                raise

            emit_error(exc.format_message(), json_output=True)
            ctx.exit(exc.exit_code)
