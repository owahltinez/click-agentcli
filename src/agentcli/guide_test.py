"""The manual ships in the binary, so it cannot go stale."""

from __future__ import annotations

from click.testing import CliRunner

from agentcli.guide import guide_command


def test_guide_prints_the_text_stripped() -> None:
    result = CliRunner().invoke(guide_command("\n  body  \n\n"), [])

    assert result.exit_code == 0
    assert result.output == "body\n"
