"""A thing you could eat, and the macros you would decide on.

Two questions have the same shape — "what can I cook under 400 kcal a serving
with 30 g of protein" and "where can I eat out under 800 kcal with 35 g" — so
the tools that answer them emit the same record. A recipe and a restaurant dish
differ in how they came to exist and in what detail they can show, not in what
a decision needs from them.

That shared part lives here so an orchestrator merges two JSON streams and
ranks, instead of special-casing each tool. Everything kind-specific goes under
`detail`, which nothing shared ever reads.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import click

MACRO_KEYS = ("kcal", "protein", "fat", "carbs")

KINDS = ("recipe", "meal")


def candidate(
    *,
    kind: str,
    identifier: str,
    name: str,
    per_serving: dict[str, float | None],
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One comparable option, per serving.

    `per_serving` carries every macro the source published and omits the rest.
    A macro is never defaulted to zero to fill the shape: a dish whose fat was
    never measured is not a fat-free dish, and `complete` is what tells them
    apart.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown candidate kind: {kind}")

    macros = {
        key: per_serving[key]
        for key in MACRO_KEYS
        if per_serving.get(key) is not None
    }
    return {
        "kind": kind,
        "id": identifier,
        "name": name,
        "per_serving": macros,
        "complete": len(macros) == len(MACRO_KEYS),
        "detail": detail or {},
    }


def macro_options(f: Callable[..., Any]) -> Callable[..., Any]:
    """The two filters every candidate source accepts, spelled identically."""
    f = click.option(
        "--min-protein",
        type=click.FloatRange(min=0),
        help="Least protein, in grams per serving.",
    )(f)
    return click.option(
        "--max-kcal",
        type=click.FloatRange(min=0),
        help="Most energy, in kcal per serving.",
    )(f)


def matches(
    record: dict[str, Any],
    *,
    max_kcal: float | None = None,
    min_protein: float | None = None,
) -> bool:
    """Whether a candidate provably satisfies the constraints.

    A candidate missing the macro a filter asks about is excluded, because it
    cannot be shown to pass. Callers report those separately rather than
    dropping them silently — "no results" and "three results I could not check"
    are different answers.
    """
    macros = record["per_serving"]
    kcal, protein = macros.get("kcal"), macros.get("protein")

    # A missing macro fails the filter that asks about it rather than being
    # treated as zero, which would pass every ceiling and fail every floor.
    over = max_kcal is not None and (kcal is None or kcal > max_kcal)
    under = min_protein is not None and (
        protein is None or protein < min_protein
    )

    return not (over or under)


def unverifiable(
    record: dict[str, Any],
    *,
    max_kcal: float | None = None,
    min_protein: float | None = None,
) -> bool:
    """Whether a filter was asked about a macro this candidate lacks."""
    macros = record["per_serving"]
    return (max_kcal is not None and macros.get("kcal") is None) or (
        min_protein is not None and macros.get("protein") is None
    )


def rank(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Protein per 100 kcal, descending. Ties break by name, not file order.

    One ranking for both kinds, so a merged list is ordered the same way
    whoever produced it. Deterministic on every machine: no locale collation.
    """

    def key(record: dict[str, Any]) -> tuple[float, float, str]:
        macros = record["per_serving"]

        # `or 0.0` would be wrong here, and wrong in the one file that defines
        # the missing-value contract: a published 0 kcal is a fact about black
        # coffee, not an absent measurement. They coincide in the arithmetic
        # below but must not coincide in the idiom.
        kcal = macros.get("kcal")
        protein = macros.get("protein")

        density = (
            protein / kcal * 100
            if kcal is not None and protein is not None and kcal > 0
            else 0.0
        )

        return (
            -density,
            -(protein if protein is not None else 0.0),
            record["name"],
        )

    return sorted(records, key=key)
