"""Shared conventions for agent-facing CLI tools."""

from agentcli.candidates import (
    KINDS,
    candidate,
    macro_options,
    matches,
    rank,
    unverifiable,
)
from agentcli.exits import (
    AssertionFailure,
    RemoteError,
    StrictFailure,
    UsageError,
)
from agentcli.group import JsonAwareGroup
from agentcli.guide import guide_command
from agentcli.output import dumps, emit, emit_error, json_option, limit_option
from agentcli.skill import SkillGroup, refresh_skill, skill_group

__all__ = [
    "KINDS",
    "AssertionFailure",
    "JsonAwareGroup",
    "RemoteError",
    "SkillGroup",
    "StrictFailure",
    "UsageError",
    "candidate",
    "dumps",
    "emit",
    "emit_error",
    "guide_command",
    "json_option",
    "limit_option",
    "macro_options",
    "matches",
    "rank",
    "refresh_skill",
    "skill_group",
    "unverifiable",
]
