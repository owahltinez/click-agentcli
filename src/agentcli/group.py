"""A command group that keeps the `--json` promise even when it fails.

Requesting JSON is a promise that stdout is parseable, and that has to hold for
failures too. The hard case is a bad flag: click raises before any subcommand
has parsed `--json`, so at that point nothing in the parsed context knows JSON
was wanted. The raw argument list is the only place it is known that early.

This lives here rather than in each tool because every tool has the same
problem, and a tool that solves it locally is a tool the next one forgets to
copy.
"""

import os
import sys
from collections.abc import Sequence
from typing import Any, Literal, NoReturn, overload

import click

from agentcli.output import emit_error
from agentcli.skill import SkillGroup

# Set to any non-empty value to keep a run from touching the skills on disk.
NO_REFRESH_ENV = "AGENTCLI_NO_SKILL_REFRESH"


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

        # Only when this process *is* the tool's command line, which is what
        # reading `sys.argv` means. A caller passing its own arguments -- an
        # embedding, or a consumer's `CliRunner` test -- gets no writes under
        # `~` it never asked for.
        if args is None:
            self._refresh_skill(arguments)

        return super().main(
            arguments,
            prog_name,
            complete_var,
            standalone_mode,
            **extra,
        )

    def _refresh_skill(self, arguments: Sequence[str]) -> None:
        """Bring already-installed copies of this tool's skill up to date.

        Upgrading a package never touched a skill already on disk, so the copy
        an agent read could sit releases behind the binary it documents, and
        nothing said so. Doing it here makes `uv tool upgrade` the whole
        workflow: no second command per tool to remember.

        Not done for `skill` itself. That group is where drift is reported and
        acted on, and a `skill status` that silently repaired what it was
        about to describe would have nothing left to report.
        """
        if os.environ.get(NO_REFRESH_ENV):
            return

        if "skill" in arguments:
            return

        skill = self.commands.get("skill")
        if isinstance(skill, SkillGroup):
            skill.refresh()

    def _drift_hint(self, ctx: click.Context, exc: Exception) -> str:
        """Why a name this caller expected might not exist.

        A caller reading a skill older than the binary asks for a command the
        binary has since renamed or dropped, and no other failure looks like
        this. The hint lives here rather than in the skill because the skill
        is the thing that went stale, while the binary is what was upgraded.
        """
        if not isinstance(exc, click.NoSuchOption | click.UsageError):
            return ""
        if "No such command" not in str(exc) and not isinstance(
            exc, click.NoSuchOption
        ):
            return ""
        if "skill" not in self.commands:
            return ""
        tool = ctx.find_root().info_name or "this tool"
        return (
            f" If this was documented, the installed skill predates this "
            f"version; run `{tool} skill install`."
        )

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except click.ClickException as exc:
            hint = self._drift_hint(ctx, exc)
            # The human path stays click's own, which prints the usage block
            # too: worth more to a person than a uniform shape.
            if not self._json_requested:
                if hint:
                    # Re-raised rather than edited: click marks the message
                    # final, and a UsageError still prints usage and exits 2.
                    raise click.UsageError(
                        f"{exc.format_message()}{hint}",
                        ctx=getattr(exc, "ctx", None),
                    ) from exc
                raise

            emit_error(f"{exc.format_message()}{hint}", json_output=True)
            ctx.exit(exc.exit_code)
