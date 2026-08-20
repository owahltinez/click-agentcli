"""Deterministic exit codes, so an agent never has to read prose.

| 0 | success                                                     |
| 1 | usage error: bad flags, unparseable input, refused input     |
| 2 | remote error: network, API, or a site refusal (never retried) |
| 3 | assertion failure: a caller-stated expectation did not hold  |
| 4 | data-quality warning escalated by `--strict`                 |

`click.ClickException` is the base because click already routes it to stderr
with a nonzero status; only the code differs per class.
"""

from __future__ import annotations

import click

# Click exits 2 for its own parse failures, which this table documents as a
# remote error. Agents are told to branch on these codes, and reading a
# mistyped flag as a transient outage invites a pointless retry, so click is
# brought into line rather than the documentation bent around it.
#
# Applied on import of `agentcli`, which every tool does, because the same
# correction living in each tool's cli.py is one a new tool silently forgets.
click.UsageError.exit_code = 1


class UsageError(click.ClickException):
    """The request could not be understood or was refused as stated."""

    exit_code = 1


class RemoteError(click.ClickException):
    """A remote source refused or could not answer. Never retried."""

    exit_code = 2


class AssertionFailure(click.ClickException):
    """Something the caller asserted turned out not to hold."""

    exit_code = 3


class StrictFailure(click.ClickException):
    """Warnings were raised and `--strict` makes them fatal."""

    exit_code = 4
