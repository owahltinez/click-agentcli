# agentcli

Shared conventions for command-line tools whose primary callers are agents.
It owns no food domain: it owns predictable errors, JSON output, skills,
in-binary guides, and the candidate record used for composition.

## Use it in a package

Install the distribution (the import name remains `agentcli`):

```sh
uv add click-agentcli
```

Register the shared output, guide, and skill commands on a Click CLI:

```python
import click

from agentcli import (
    JsonAwareGroup,
    emit,
    guide_command,
    json_option,
    skill_group,
)

GUIDE = """# acme guide

Use `acme hello` to print a greeting.
"""


@click.group(cls=JsonAwareGroup)
def cli() -> None:
    """Acme's agent-facing CLI."""


@cli.command()
@json_option
def hello(json_output: bool) -> None:
    """Print a greeting."""
    emit(
        {"message": "hello"},
        json_output=json_output,
        human=lambda result: [result["message"]],
    )


cli.add_command(guide_command(GUIDE))
cli.add_command(skill_group(name="acme", package="acme"))
```

The command has readable output for people and one stable document for agents:

```console
$ acme hello
hello
$ acme hello --json
{"ok":true,"data":{"message":"hello"}}
$ acme guide
# acme guide
...
$ acme skill install
copied  /home/me/.agents/skills/acme
```

Declare the entry point and ship the skill inside the import package. For
Hatchling, a project-root `SKILL.md` can be mapped into the required wheel
location like this:

```toml
[project.scripts]
acme = "acme.cli:cli"

[tool.hatch.build.targets.wheel.force-include]
"SKILL.md" = "acme/skills/acme/SKILL.md"
```

`skill_group(name="acme", package="acme")` expects an installed wheel to
contain `acme/skills/acme/SKILL.md`. The same file may stay at the repository
root for source-checkout use. Its frontmatter name must match the skill name:

```markdown
---
name: acme
description: Use Acme from an agent.
---

Run `acme guide` for the complete manual.
```

Add `@json_option` to each command that supports structured output and call
`emit` once with both the data and its human renderer. `JsonAwareGroup` then
keeps parse failures structured when `--json` was requested.

## Develop this package

```sh
uv sync --project .
uv run --project . pytest -q
```

## CLI contract

Every consuming tool uses `click`, declares `--json` per command with
`json_option`, and makes its top-level group `JsonAwareGroup`. The group scans
raw arguments so even parse failures that happen before a subcommand exists
honour a `--json` request. Importing `agentcli.exits` also changes Click's own
usage-error code from 2 to 1; consumers must not repeat that correction.

| code | meaning |
| --- | --- |
| 0 | success |
| 1 | usage error or a caller-liftable refusal |
| 2 | remote, network, or site failure after allowed retries |
| 3 | a caller-stated assertion did not hold |
| 4 | a data-quality warning escalated by `--strict` |

An exhausted request budget is code 1, because the caller can lift it. A
proportional recipe fit with no solution is code 3.

`--json` emits exactly one JSON object on stdout and nothing else. Success and
failure are symmetric:

```json
{"ok":true,"data":{}}
{"ok":false,"error":{"message":"..."}}
```

A search with no matches is successful with an empty list. Under `--json`,
errors go to stdout so a caller never has to merge streams to recover the one
promised document. Human errors go to stderr.

The stable public surface is:

- `UsageError`, `RemoteError`, `AssertionFailure`, and `StrictFailure`.
- `dumps`, `emit`, `emit_error`, `json_option`, and `limit_option`.
- `JsonAwareGroup` for every consuming tool's top-level group.
- `skill_group(name=..., package=...)` for `skill install`, `uninstall`, and
  `status`. Installation refuses an unrelated destination, recognises owned
  broken symlinks an older version left, always copies, and supports `--to`
  and `--dry-run`. `status` compares each installed copy against the packaged
  one and reports `current` or `stale`, because upgrading a package never
  refreshes a skill already on disk. It never links: a link points into the environment, whose
  path carries the interpreter version, so a rebuild elsewhere leaves the
  skill silently absent rather than merely stale. With no options it installs everywhere the skill is wanted
  and refreshes its own earlier copies, so plain `install` is the whole
  job; a directory holding somebody else's skill is still refused.
- `guide_command(text)` for a complete manual available without a network.
- `candidate`, `macro_options`, `matches`, `rank`, and `unverifiable` for the
  shared composition record and filters below.

## Candidate contract

Candidate sources answer the same question: filter things someone could eat by
per-serving figures, then rank them with provenance. Recipes and restaurant
meals therefore emit the same record:

```json
{
  "kind":"recipe",
  "id":"sourdough-pizza",
  "name":"Sourdough Pizza",
  "per_serving":{"kcal":384.2,"protein":31.5,"fat":12.1,"carbs":38.4},
  "complete":true,
  "detail":{}
}
```

`kind` is `recipe` or `meal`. `id` is accepted back by the emitting tool;
display-only slugs are not identifiers. Source-specific fields live under
`detail`, which shared code never reads.

Sources accept `macro_options` (`--max-kcal`, `--min-protein`) and use `rank`.
The rank key is unrounded protein per 100 kcal, then absolute protein, then
name. `--max-kcal 0` is valid because zero-calorie records exist.

`per_serving` contains only figures actually known by the source, whatever
they are — a source that also publishes fibre or sodium puts them here.
Missing is never filled with zero. `candidate` takes the keys that count as a
full set from its caller as `required`, because this package does not know what
a macro is; callers answering the same question share that tuple so `complete`
keeps meaning the same thing across them. A candidate missing a requested
filter figure is excluded and returned in the source's `unverifiable` or
equivalent bucket. Every source emits that bucket, even when its loader makes
it structurally empty.

This contract is the reason the tools can be independent packages: an
orchestrator can merge and rank results without knowing which source answered.
