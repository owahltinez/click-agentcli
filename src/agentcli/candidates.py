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

from collections.abc import Callable
from typing import Any

import click

KINDS = ("recipe", "meal")


def candidate(
    *,
    kind: str,
    identifier: str,
    name: str,
    per_serving: dict[str, float | None],
    required: tuple[str, ...],
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One comparable option, per serving.

    `per_serving` carries every figure the source published, whatever they are,
    and omits the rest. A figure is never defaulted to zero to fill the shape:
    a dish whose fat was never measured is not a fat-free dish, and `complete`
    is what tells them apart.

    `required` is the caller's -- which figures it considers a full set. This
    module does not know what a macro is, and a source that publishes fibre or
    sodium should be able to say so without asking permission here. Callers
    that answer the same question share the tuple so `complete` keeps meaning
    the same thing across them.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown candidate kind: {kind}")

    published = {
        key: value for key, value in per_serving.items() if value is not None
    }
    return {
        "kind": kind,
        "id": identifier,
        "name": name,
        "per_serving": published,
        "complete": all(key in published for key in required),
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

    A candidate missing the figure a filter asks about is excluded, because it
    cannot be shown to pass. Callers report those separately rather than
    dropping them silently — "no results" and "three results I could not check"
    are different answers.
    """
    published = record["per_serving"]
    kcal, protein = published.get("kcal"), published.get("protein")

    # A missing figure fails the filter that asks about it rather than being
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
