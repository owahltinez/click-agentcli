"""`<tool> skill` — put the packaged Agent Skill where agents find it.

Installing the CLI is not enough: agents discover skills by scanning specific
directories, and those directories are per-tool. `~/.agents/skills` is the
emerging cross-tool location, but several tools only read their own. So rather
than guess, `install` writes to the shared location and reports every tool
directory it can see, with the command to cover those too.

Parameterised by skill name and package because the two hand-written copies
this replaces had already drifted apart in exactly the guards that matter.
"""

import shutil
from collections.abc import Iterable
from importlib import resources
from pathlib import Path
from typing import Any

import click

from agentcli.output import emit, json_option

# The tool-agnostic location. Read by Gemini CLI and others; a reasonable
# default even where a tool also keeps its own directory.
SHARED_DIR = Path(".agents") / "skills"

# Per-tool directories, keyed by the marker that shows the tool is installed.
TOOL_DIRS: dict[str, tuple[Path, Path]] = {
    "Claude Code": (Path(".claude"), Path(".claude") / "skills"),
    "Gemini CLI": (Path(".gemini"), Path(".gemini") / "skills"),
    "Antigravity": (Path(".gemini"), Path(".gemini") / "config" / "skills"),
    "Cursor": (Path(".cursor"), Path(".cursor") / "skills"),
}


def _package_root(package: str) -> Path | None:
    """The installed directory of the consuming tool's package."""
    try:
        return Path(str(resources.files(package)))
    except (ModuleNotFoundError, TypeError):
        # A tool that cannot import its own package has larger problems than
        # a missing skill, and a traceback here would only hide them.
        return None


def _skill_candidates(name: str, package: str) -> list[Path]:
    """Every place SKILL.md is allowed to live, best first.

    SKILL.md is authored at the repository root, where it is visible, and
    mapped into the package at build time. Both have to work: the packaged
    path for real installs, the checkout path when running from source. The
    root is one level above the package for a flat layout and two for `src/`.
    """
    root = _package_root(package)
    if root is None:
        return []

    packaged = root / "skills" / name / "SKILL.md"
    return [packaged] + [p / "SKILL.md" for p in list(root.parents)[:2]]


def packaged_skill(*, name: str, package: str) -> Path:
    """Locate SKILL.md, whether running from a wheel or a source checkout."""
    candidates = _skill_candidates(name, package)
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    if not candidates:
        raise click.ClickException(f"package {package} is not importable")

    looked = ", ".join(str(candidate) for candidate in candidates)
    raise click.ClickException(f"SKILL.md not found; looked in {looked}")


def detected_tools(home: Path) -> dict[str, Path]:
    """Return skills directories for the agent tools present on this machine."""
    return {
        label: home / skills
        for label, (marker, skills) in TOOL_DIRS.items()
        if (home / marker).is_dir()
    }


def _primary_target(destination: Path | None, home: Path, name: str) -> Path:
    """The one location acted on when no sweep was requested.

    A repository-scoped install is just `--to .agents/skills`, so it needs no
    flag of its own.
    """
    if destination is not None:
        return destination / name
    return home / SHARED_DIR / name


def _is_our_skill(target: Path, *, name: str) -> bool:
    """Does this directory actually hold the skill we installed?

    A broken symlink counts. Older versions could install one, and refusing to
    clean up exactly that wreckage would be perverse -- the directory is still
    one this tool created.
    """
    manifest = target / "SKILL.md"
    if manifest.is_symlink() and not manifest.exists():
        return True

    if not manifest.is_file():
        return False

    # Unreadable or not text: not something this tool wrote, and certainly not
    # something to delete on the strength of a guess.
    try:
        text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    return f"name: {name}" in text


def _remove(target: Path) -> None:
    """Delete an installed skill, link or directory alike."""
    if target.is_symlink() or target.is_file():
        target.unlink()
    else:
        shutil.rmtree(target)


def _refusal(target: Path, *, name: str) -> str | None:
    """Why `install` would decline this target, or None if it would proceed.

    One function so the guard and the `--dry-run` prediction cannot drift: a
    dry run that promises a success the real command declines is worse than no
    dry run at all.

    Replacing *our own* skill needs no permission: the packaged manifest is
    the source of truth, so re-installing is idempotent and is how a stale
    copy gets refreshed. Whatever happens to sit at a mistyped `--to` is a
    different matter -- deleting a tree is not something to do on the
    strength of its name.
    """
    if not target.exists() and not target.is_symlink():
        return None

    if not _is_our_skill(target, name=name):
        return (
            f"{target} exists and does not contain the {name} skill; "
            f"refusing to replace it. Remove it by hand if that is really "
            f"intended."
        )

    return None


def _place(source: Path, target: Path, *, name: str) -> str:
    """Place SKILL.md into a skill directory of its own.

    Always a copy. A link points into the environment this CLI was installed
    into, and that path is not stable: it carries the interpreter version, so
    an environment rebuilt on another Python leaves a dangling link and the
    skill silently disappears. Copying costs a stale skill after an upgrade,
    which is the louder failure and the cheaper one -- the skill is a router,
    and the manual it routes to (`<tool> guide`) ships in the binary.
    """
    refusal = _refusal(target, name=name)
    if refusal is not None:
        raise click.ClickException(refusal)

    if target.exists() or target.is_symlink():
        _remove(target)

    # The directory is named for the skill, as the spec requires.
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target / "SKILL.md")
    return f"copied  {target}"


def _state(path: Path, source: Path | None, *, name: str) -> str:
    """Whether a location holds this skill, and whether it is the current one.

    Presence alone said `installed` for a copy many releases old, because
    upgrading a package never refreshes a skill already on disk. Comparing the
    bytes is what makes that drift visible instead of silent.
    """
    if not _is_our_skill(path, name=name):
        return "absent"
    if source is None or not source.is_file():
        return "installed"
    installed = path / "SKILL.md"
    try:
        return (
            "current"
            if installed.read_bytes() == source.read_bytes()
            else "stale"
        )
    except OSError:
        return "installed"


def _status_rows(
    home: Path, name: str, source: Path | None = None
) -> list[dict[str, Any]]:
    """One row per known location, shared first, whether present or not."""
    locations = [("Shared (.agents)", home / SHARED_DIR / name)]
    locations += [
        (label, home / skills / name)
        for label, (_, skills) in TOOL_DIRS.items()
    ]

    rows = []
    for label, path in locations:
        state = _state(path, source, name=name)
        rows.append(
            {
                "tool": label,
                "path": str(path),
                "installed": state != "absent",
                "state": state,
            }
        )
    return rows


def _status_lines(payload: dict[str, Any]) -> Iterable[str]:
    """Render `status` for a human: fixed columns, no table drawing."""
    for row in payload["locations"]:
        mark = "-" if row["state"] == "absent" else row["state"]
        yield f"{mark:<10} {row['tool']:<16} {row['path']}"

    if any(row["state"] == "stale" for row in payload["locations"]):
        yield (
            f"A stale copy predates this version. Run "
            f"`{payload['skill']} skill install` to refresh it."
        )


def skill_group(*, name: str, package: str) -> click.Group:
    """Build the `skill` command group for one tool.

    `name` is both the skill name and the binary that carries it, so it also
    spells the commands printed in help text.
    """
    target_help = (
        f"A skills directory to act on. The skill lives in a `{name}` "
        "subdirectory of it, as the spec requires."
    )
    dry_run_help = "Print what would happen without touching the filesystem."

    @click.group("skill")
    def skill() -> None:
        """Install the packaged Agent Skill so agents can discover this tool."""

    @skill.command(
        "install",
        epilog=f"""Examples:

\b
  {name} skill install                       # everywhere it is wanted
  {name} skill install --to ~/.claude/skills # just this one
  {name} skill install --to .agents/skills   # this repository only""",
    )
    @click.option(
        "--to",
        "destination",
        type=click.Path(file_okay=False, path_type=Path),
        help=target_help,
    )
    @click.option("--dry-run", is_flag=True, help=dry_run_help)
    def install_command(
        destination: Path | None,
        dry_run: bool,
    ) -> None:
        """Install the Agent Skill into an agent's skills directory.

        With no options this installs everywhere the skill is wanted: the
        cross-tool ~/.agents/skills, plus the own skills directory of every
        agent tool detected on this machine. A tool that is not installed is
        never created. Use --to to target exactly one directory instead.
        """
        source = packaged_skill(name=name, package=package)

        home = Path.home()
        targets = [_primary_target(destination, home, name)]

        # `--to` is the way to ask for one directory, so it opts out of the
        # sweep rather than adding to it.
        if destination is None:
            targets += [
                directory / name for directory in detected_tools(home).values()
            ]

        # Two tools can name the same directory, and `--to` can name one a
        # sweep already covers; installing twice would report a spurious
        # "already exists".
        for target in dict.fromkeys(targets):
            if dry_run:
                refusal = _refusal(target, name=name)
                if refusal is None:
                    click.echo(f"would install  {target}")
                else:
                    click.echo(f"would REFUSE   {refusal}")
                continue

            try:
                click.echo(_place(source, target, name=name))
            except OSError as exc:
                raise click.ClickException(
                    f"could not install into {target}: {exc.strerror or exc}"
                ) from None

    @skill.command(
        "uninstall",
        epilog=f"""Examples:

\b
  {name} skill uninstall                     # every known location
  {name} skill uninstall --to ~/.claude/skills
  {name} skill uninstall --to .agents/skills""",
    )
    @click.option(
        "--to",
        "destination",
        type=click.Path(file_okay=False, path_type=Path),
        help=target_help,
    )
    @click.option("--dry-run", is_flag=True, help=dry_run_help)
    def uninstall_command(destination: Path | None, dry_run: bool) -> None:
        """Remove the Agent Skill from an agent's skills directory.

        With no options this mirrors install and clears every known location,
        so an install cannot strand copies an uninstall then leaves behind.
        Only ever removes a directory that actually holds this skill, so
        pointing --to somewhere unexpected fails rather than deleting
        someone's work.
        """
        home = Path.home()
        targets = [_primary_target(destination, home, name)]

        # Cleanup covers every known location rather than only the tools still
        # present: an uninstalled tool can leave a skill behind, and that is
        # exactly what needs removing. Missing ones are skipped quietly.
        if destination is None:
            targets += [
                home / skills / name for _, skills in TOOL_DIRS.values()
            ]

        removed = 0
        for target in dict.fromkeys(targets):
            if not target.exists() and not target.is_symlink():
                continue

            if not _is_our_skill(target, name=name):
                raise click.ClickException(
                    f"{target} does not contain the {name} skill; refusing "
                    f"to delete it. Remove it by hand if that is really "
                    f"intended."
                )

            if dry_run:
                click.echo(f"would remove  {target}")
            else:
                try:
                    _remove(target)
                except OSError as exc:
                    raise click.ClickException(
                        f"could not remove {target}: {exc.strerror or exc}"
                    ) from None
                click.echo(f"removed  {target}")
            removed += 1

        if not removed:
            click.echo("nothing to remove")

    @skill.command("status")
    @json_option
    def status_command(json_output: bool) -> None:
        """Show every known location and whether the skill is installed."""
        try:
            source: Path | None = packaged_skill(name=name, package=package)
        except click.ClickException:
            source = None
        payload = {
            "skill": name,
            "locations": _status_rows(Path.home(), name, source),
        }
        emit(payload, json_output=json_output, human=_status_lines)

    return skill
