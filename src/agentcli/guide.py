"""`<tool> guide` — the manual, shipped inside the binary.

The skill file stays a thin router precisely because the detail lives here: a
skill installed once goes stale, while the guide is upgraded with the package
that implements it.
"""

from __future__ import annotations

import click


def guide_command(text: str) -> click.Command:
    """Build the `guide` subcommand for a tool's own manual text."""

    @click.command("guide")
    def guide() -> None:
        """Print the full agent-facing manual for this tool."""
        click.echo(text.strip())

    return guide
