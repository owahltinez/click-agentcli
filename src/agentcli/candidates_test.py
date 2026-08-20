"""The contract two independent tools have to agree on without talking."""

from __future__ import annotations

import itertools

import click
import pytest
from click.testing import CliRunner

from agentcli.candidates import (
    candidate,
    macro_options,
    matches,
    rank,
    unverifiable,
)

FULL = {"kcal": 384.2, "protein": 31.5, "fat": 12.1, "carbs": 38.4}
PARTIAL = {"kcal": 540.0, "protein": 45.8, "fat": None, "carbs": None}


def make(kind: str = "recipe", name: str = "Thing", **macros: object) -> dict:
    return candidate(
        kind=kind,
        identifier=name.lower(),
        name=name,
        per_serving=macros or FULL,
    )


def test_an_unpublished_macro_is_omitted_rather_than_zeroed() -> None:
    """A dish whose fat was never measured is not a fat-free dish."""
    partial = candidate(
        kind="meal", identifier="x", name="Bowl", per_serving=PARTIAL
    )

    assert partial["per_serving"] == {"kcal": 540.0, "protein": 45.8}
    assert "fat" not in partial["per_serving"]
    assert partial["complete"] is False


def test_complete_means_all_four_macros_present() -> None:
    assert make()["complete"] is True
    assert make(kcal=1.0, protein=1.0, fat=1.0)["complete"] is False


def test_detail_is_where_the_kinds_differ() -> None:
    """Nothing shared reads `detail`, so the kinds cannot collide in it."""
    meal = candidate(
        kind="meal",
        identifier="crust-margherita",
        name="Margherita",
        per_serving=FULL,
        detail={"restaurant": "Crust Pizza", "distance_km": 1.5},
    )

    assert meal["detail"]["restaurant"] == "Crust Pizza"
    assert set(meal) == {
        "kind",
        "id",
        "name",
        "per_serving",
        "complete",
        "detail",
    }


def test_an_unknown_kind_is_refused() -> None:
    """A third kind is a decision, not something a caller slips in."""
    with pytest.raises(ValueError, match="unknown candidate kind"):
        candidate(kind="snack", identifier="x", name="X", per_serving=FULL)


@pytest.mark.parametrize(
    ("max_kcal", "expected"), [(384.2, True), (384.1, False), (500.0, True)]
)
def test_the_calorie_ceiling_is_inclusive(
    max_kcal: float, expected: bool
) -> None:
    assert matches(make(), max_kcal=max_kcal) is expected


@pytest.mark.parametrize(
    ("min_protein", "expected"), [(31.5, True), (31.6, False), (10.0, True)]
)
def test_the_protein_floor_is_inclusive(
    min_protein: float, expected: bool
) -> None:
    assert matches(make(), min_protein=min_protein) is expected


def test_a_missing_macro_fails_the_filter_that_asks_about_it() -> None:
    """Treating it as zero would pass every ceiling and fail every floor."""
    no_kcal = make(protein=40.0, fat=1.0, carbs=1.0)

    assert matches(no_kcal, max_kcal=800) is False
    # And it is not silently a miss: the caller can say why it could not check.
    assert unverifiable(no_kcal, max_kcal=800) is True
    # A filter about a macro it does have is answerable as normal.
    assert matches(no_kcal, min_protein=30) is True
    assert unverifiable(no_kcal, min_protein=30) is False


def test_no_filters_matches_everything_including_incomplete() -> None:
    partial = candidate(
        kind="meal", identifier="x", name="Bowl", per_serving=PARTIAL
    )

    assert matches(partial) is True
    assert unverifiable(partial) is False


def test_ranking_is_protein_density_then_name() -> None:
    dense = make(name="Dense", kcal=200.0, protein=30.0, fat=1.0, carbs=1.0)
    lean = make(name="Lean", kcal=600.0, protein=30.0, fat=1.0, carbs=1.0)

    assert [c["name"] for c in rank([lean, dense])] == ["Dense", "Lean"]


def test_ranking_does_not_depend_on_input_order() -> None:
    """A merged list must be ordered the same whoever produced it."""
    tied = [
        make(name=name, kcal=400.0, protein=20.0, fat=1.0, carbs=1.0)
        for name in ("Beta", "Alpha", "Gamma")
    ]

    orders = {
        tuple(c["name"] for c in rank(list(p)))
        for p in itertools.permutations(tied)
    }

    assert orders == {("Alpha", "Beta", "Gamma")}


def test_a_zero_calorie_candidate_does_not_divide_by_zero() -> None:
    """Water and black coffee are real entries, not arithmetic hazards."""
    zero = make(name="Water", kcal=0.0, protein=0.0, fat=0.0, carbs=0.0)

    assert [c["name"] for c in rank([zero, make(name="Food")])] == [
        "Food",
        "Water",
    ]


def test_both_tools_spell_the_filters_identically() -> None:
    """The flags are shared code precisely so they cannot drift apart."""

    @click.command()
    @macro_options
    def cli(max_kcal: float | None, min_protein: float | None) -> None:
        click.echo(f"{max_kcal} {min_protein}")

    runner = CliRunner()
    assert runner.invoke(cli, []).output.strip() == "None None"
    ok = runner.invoke(cli, ["--max-kcal", "400", "--min-protein", "30"])
    assert ok.output.strip() == "400.0 30.0"

    # A negative ceiling is a mistake, not something to clamp silently.
    assert runner.invoke(cli, ["--max-kcal", "-1"]).exit_code == 1


def test_a_published_zero_is_not_a_missing_measurement() -> None:
    """The distinction this whole project turns on, in the file that defines it.

    Black coffee at 0 kcal is a measured fact; a dish whose energy was never
    recorded is not. They happen to rank the same, so only the idiom keeps them
    apart -- which is exactly why it is asserted here rather than assumed.
    """
    measured = candidate(
        kind="meal",
        identifier="coffee",
        name="Black Coffee",
        per_serving={"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0},
    )
    unmeasured = candidate(
        kind="meal",
        identifier="mystery",
        name="Mystery",
        per_serving={"protein": 0.0, "fat": 0.0, "carbs": 0.0},
    )

    assert measured["per_serving"]["kcal"] == 0.0
    assert measured["complete"] is True

    assert "kcal" not in unmeasured["per_serving"]
    assert unmeasured["complete"] is False

    # A calorie ceiling is answerable for one and not the other.
    assert matches(measured, max_kcal=100) is True
    assert matches(unmeasured, max_kcal=100) is False
    assert unverifiable(unmeasured, max_kcal=100) is True

    # And neither divides by zero on the way through the shared ranking.
    assert len(rank([measured, unmeasured])) == 2
