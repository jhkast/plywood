import random

import pytest

from plywood.core.models import Grain, Part, Settings, Stock, StockKind
from plywood.core.optimize import optimize
from plywood.core.validate import check

IN = 25.4
PLY = 23 / 32 * IN


def fast(**kw) -> Settings:
    """Deterministic: full heuristic sweep, no random restarts."""
    return Settings(time_budget=30, random_iterations=0, seed=0, **kw)


def sheet(qty=None, cost=0.0, length=96, width=48, thickness=PLY, tag=None, name="ply"):
    return Stock(name, length * IN, width * IN, thickness, qty=qty, cost=cost, tag=tag)


def part(name, length, width, qty=1, thickness=PLY, grain=Grain.NONE, tag=None, kind=StockKind.SHEET):
    return Part(name, length * IN, width * IN, thickness, qty=qty, grain=grain, tag=tag, kind=kind)


def placed(result):
    return sum(len(lay.placements) for lay in result.layouts)


def test_exact_fit_without_kerf():
    r = optimize([part("q", 24, 48, qty=4)], [sheet()], fast(rip_kerf=0, crosscut_kerf=0))
    assert len(r.layouts) == 1 and placed(r) == 4 and check(r) == []


def test_kerf_forces_second_sheet():
    r = optimize([part("q", 24, 48, qty=4)], [sheet()], fast())
    assert len(r.layouts) == 2 and placed(r) == 4 and check(r) == []


@pytest.mark.parametrize("seed", range(25))
def test_random_jobs_are_valid(seed):
    rng = random.Random(seed)
    parts = [
        part(f"p{i}", rng.uniform(2, 60), rng.uniform(2, 40), qty=rng.randint(1, 4),
             grain=rng.choice(list(Grain)))
        for i in range(rng.randint(1, 15))
    ]
    stocks = [sheet(cost=80), sheet(qty=2, length=40, width=30, name="offcut")]
    settings = fast(edge_trim=rng.choice([0, 0.25 * IN]))
    settings.time_budget = 0.5  # cap the sweep for big random jobs
    r = optimize(parts, stocks, settings)
    assert check(r) == []
    total = sum(p.qty for p in parts)
    assert placed(r) + sum(u.count for u in r.unplaced) == total


def test_grain_lock_respected():
    # 40" long part locked along the grain cannot fit a 30" long offcut, even though rotated it would.
    p = part("door", 40, 20, grain=Grain.LENGTH)
    r = optimize([p], [sheet(qty=1, length=30, width=44)], fast(default_sheets=False))
    assert placed(r) == 0 and r.unplaced
    r = optimize([part("door", 40, 20)], [sheet(qty=1, length=30, width=44)], fast(default_sheets=False))
    assert placed(r) == 1 and r.layouts[0].placements[0].rotated


def test_on_hand_used_before_buying():
    stocks = [sheet(cost=85), sheet(qty=1, length=40, width=30, name="offcut")]
    r = optimize([part("small", 20, 10, qty=2)], stocks, fast())
    assert [lay.stock.name for lay in r.layouts] == ["offcut"]
    assert r.purchase_cost == 0


def test_on_hand_count_is_a_limit():
    stocks = [sheet(qty=1, length=24, width=24, name="scrap")]
    r = optimize([part("sq", 20, 20, qty=3)], stocks, fast(default_sheets=False))
    assert placed(r) == 1
    assert r.unplaced[0].count == 2 and "on-hand" in r.unplaced[0].reason


def test_thickness_matching_and_default_sheets():
    half = part("drawer", 20, 5, thickness=0.5 * IN)
    r = optimize([half, part("side", 30, 20)], [sheet()], fast())
    by_name = {p.part.name: lay.stock for lay in r.layouts for p in lay.placements}
    assert by_name["side"].name == "ply"
    assert by_name["drawer"].name == "4x8 sheet (default)"
    assert by_name["drawer"].thickness == pytest.approx(0.5 * IN)


def test_no_default_sheets_flags_unmatched():
    r = optimize([part("drawer", 20, 5, thickness=0.5 * IN)], [sheet()], fast(default_sheets=False))
    assert placed(r) == 0 and "thickness" in r.unplaced[0].reason


def test_tag_restricts_stock():
    stocks = [sheet(name="birch", tag="Birch"), sheet(name="mdf", tag="mdf")]
    r = optimize([part("a", 10, 10, tag="birch"), part("b", 10, 10, tag="MDF")], stocks, fast())
    by_name = {p.part.name: lay.stock.name for lay in r.layouts for p in lay.placements}
    assert by_name == {"a": "birch", "b": "mdf"}


def test_boards_never_rotate_free_parts():
    board = Stock("1x4", 96 * IN, 3.5 * IN, 0.75 * IN, kind=StockKind.BOARD)
    r = optimize([part("rail", 30, 1.5, qty=6, thickness=0.75 * IN, kind=StockKind.BOARD)], [board], fast())
    assert check(r) == []
    assert all(not p.rotated for lay in r.layouts for p in lay.placements)
    assert len(r.layouts) == 1  # 3 per row × 2 rows on one board


def test_sheet_and_board_parts_never_mix():
    board = Stock("1x4", 96 * IN, 3.5 * IN, 0.75 * IN, kind=StockKind.BOARD)
    ply = sheet(thickness=0.75 * IN, name="ply")
    parts = [
        part("rail", 30, 1.5, qty=2, thickness=0.75 * IN, kind=StockKind.BOARD),
        part("cleat", 30, 1.5, qty=2, thickness=0.75 * IN),  # sheet part that would fit a board
    ]
    r = optimize(parts, [board, ply], fast())
    assert check(r) == []
    by_name = {p.part.name: lay.stock.name for lay in r.layouts for p in lay.placements}
    assert by_name == {"rail": "1x4", "cleat": "ply"}


def test_board_part_without_board_stock_is_unplaced():
    r = optimize([part("rail", 30, 1.5, thickness=0.75 * IN, kind=StockKind.BOARD)], [], fast())
    assert placed(r) == 0 and "no board stock" in r.unplaced[0].reason


def test_too_big_part_is_reported():
    r = optimize([part("huge", 100, 50)], [sheet()], fast())
    assert placed(r) == 0 and "too large" in r.unplaced[0].reason


def test_cut_steps_account_for_every_part():
    r = optimize([part("a", 30, 20, qty=5), part("b", 12, 7, qty=6)], [sheet()], fast())
    for lay in r.layouts:
        labels = [seg.label for step in lay.steps for seg in step.segments if seg.kind == "part"]
        assert sorted(labels) == sorted(p.label for p in lay.placements)
