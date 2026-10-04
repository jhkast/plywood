import json
import random

import pytest

from plywood.core.models import Grain, Part, Settings, Stock, StockKind
from plywood.core.optimize import optimize
from plywood.core.validate import check

IN = 25.4
PLY = 23 / 32 * IN


def fast(**kw) -> Settings:
    """Deterministic: full heuristic sweep, no random restarts."""
    return Settings(tries=0, **kw)


def sheet(qty=None, length=96, width=48, thickness=PLY, tag=None, name="ply"):
    return Stock(name, length * IN, width * IN, thickness, qty=qty, tag=tag)


def part(name, length, width, qty=1, thickness=PLY, grain=Grain.NONE, tag=None, kind=StockKind.SHEET):
    return Part(name, length * IN, width * IN, thickness, qty=qty, grain=grain, tag=tag, kind=kind)


def placed(result):
    return sum(len(lay.placements) for lay in result.layouts)


def test_exact_fit_without_kerf():
    r = optimize([part("q", 24, 48, qty=4)], [sheet()], fast(sheet_kerf=0))
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
    stocks = [sheet(), sheet(qty=2, length=40, width=30, name="offcut")]
    settings = fast(edge_trim=rng.choice([0, 0.25 * IN]))
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
    stocks = [sheet(), sheet(qty=1, length=40, width=30, name="offcut")]
    r = optimize([part("small", 20, 10, qty=2)], stocks, fast())
    assert [lay.stock.name for lay in r.layouts] == ["offcut"]
    assert r.purchased == []


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


def test_board_part_without_board_stock_is_unplaced_when_not_assuming_boards():
    r = optimize([part("rail", 30, 1.5, thickness=0.75 * IN, kind=StockKind.BOARD)], [], fast(default_boards=False))
    assert placed(r) == 0 and "no board stock" in r.unplaced[0].reason


def test_too_big_part_is_reported():
    r = optimize([part("huge", 100, 50)], [sheet()], fast())
    assert placed(r) == 0 and "too large" in r.unplaced[0].reason


def test_cut_steps_account_for_every_part():
    r = optimize([part("a", 30, 20, qty=5), part("b", 12, 7, qty=6)], [sheet()], fast())
    for lay in r.layouts:
        labels = [seg.label for step in lay.steps for seg in step.segments if seg.kind == "part"]
        assert sorted(labels) == sorted(p.label for p in lay.placements)


def test_untagged_stock_serves_tagged_parts_one_material_per_sheet():
    # The user's case: one untagged 6'x4' on hand, parts tagged ply and mdf.
    scrap = Stock("6x4", 72 * IN, 48 * IN, 0.75 * IN, qty=1)
    parts = [part("top", 27, 23.75, thickness=0.75 * IN, tag="mdf"),
             part("side", 32.5, 27, qty=2, thickness=0.75 * IN, tag="ply")]
    r = optimize(parts, [scrap], fast())
    assert check(r) == [] and placed(r) == 3
    on_hand = [lay for lay in r.layouts if lay.stock.name == "6x4"]
    assert len(on_hand) == 1 and on_hand[0].tag in ("mdf", "ply")
    # the rest come from default sheets of the other material, not left unplaced
    assert all(lay.tag in ("mdf", "ply") for lay in r.layouts)


@pytest.mark.parametrize("seed", range(6))
def test_cut_priority_never_buys_more_and_cuts_no_more(seed):
    rng = random.Random(seed)
    parts = [part(f"p{i}", rng.uniform(5, 40), rng.uniform(3, 20), qty=rng.randint(1, 5)) for i in range(10)]
    waste = optimize(parts, [sheet()], fast(priority="waste"))
    cuts = optimize(parts, [sheet()], fast(priority="cuts"))
    assert check(cuts) == []
    assert len(cuts.purchased) <= len(waste.purchased)
    assert sum(lay.cuts for lay in cuts.layouts) <= sum(lay.cuts for lay in waste.layouts)


def test_trim_only_when_edges_are_chosen():
    plain = optimize([part("big", 60, 40)], [sheet()], fast(edge_trim=0.5 * IN))
    assert plain.layouts[0].trims == (0, 0, 0, 0) and plain.layouts[0].steps[0].direction != "trim"
    trimmed = Stock("ply", 96 * IN, 48 * IN, PLY, trim_edges="lrbt")
    r = optimize([part("big", 60, 40)], [trimmed], fast(edge_trim=0.5 * IN))
    lay = r.layouts[0]
    assert check(r) == [] and lay.trims == pytest.approx((0.5 * IN,) * 4)
    assert lay.steps[0].direction == "trim" and lay.cuts >= 4


def test_oversize_packs_rough_sizes_and_keeps_final_labels():
    exact = fast(sheet_kerf=0)
    assert len(optimize([part("q", 24, 24, qty=8)], [sheet()], exact).layouts) == 1
    rough = fast(sheet_kerf=0, allowance=0.5 * IN)
    r = optimize([part("q", 24, 24, qty=8)], [sheet()], rough)
    assert len(r.layouts) == 3 and check(r) == []  # 24-1/2" squares: 3 per sheet
    p = r.layouts[0].placements[0]
    assert p.part.length == pytest.approx(24 * IN) and p.rect.w == pytest.approx(24.5 * IN)
    finals = [seg.final for lay in r.layouts for st in lay.steps for seg in st.segments if seg.kind == "part"]
    assert finals and all(f == pytest.approx((24 * IN, 24 * IN)) for f in finals)


def test_trim_only_chosen_edges():
    # 72" x 48" left over after cutting 24" off a 4x8: the right end is already clean.
    scrap = Stock("scrap", 72 * IN, 48 * IN, PLY, qty=1, trim_edges="lbt")
    r = optimize([part("panel", 71.5, 47)], [scrap], fast(edge_trim=0.25 * IN, sheet_kerf=0,
                                                         default_sheets=False))
    lay = r.layouts[0]
    assert check(r) == [] and lay.trims == pytest.approx((0.25 * IN, 0, 0.25 * IN, 0.25 * IN))
    assert lay.placements[0].rect.x == pytest.approx(0.25 * IN)


def test_no_cut_when_leftover_is_narrower_than_kerf():
    # Four 23.9" parts across a 96" sheet with 1/8" kerf: 3 cuts, then 0.025" left -- no 4th cut.
    r = optimize([part("p", 23.9, 48, qty=4)], [sheet()], fast(default_sheets=False))
    lay = r.layouts[0]
    assert check(r) == [] and len(lay.placements) == 4
    assert lay.cuts == 3
    assert all(seg.size > 0 for st in lay.steps for seg in st.segments)


# ---------------------------------------------------------------- repeatable results, saved plans


def _job():
    rng = random.Random(7)
    parts = [part(f"p{i}", rng.uniform(4, 40), rng.uniform(4, 30), qty=rng.randint(1, 3)) for i in range(12)]
    return parts, [sheet(), sheet(qty=1, length=40, width=30, name="offcut")]


def test_same_inputs_same_layout_in_a_new_process():
    import json
    import os
    import subprocess
    import sys

    code = (
        "import json, sys; sys.path.insert(0, 'tests'); from test_optimize import _job, Settings; "
        "from plywood.core.optimize import optimize; p, s = _job(); "
        "print(json.dumps(optimize(p, s, Settings(tries=200)).plan))"
    )
    runs = [
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed}).stdout
        for seed in ("1", "2")
    ]
    assert runs[0] == runs[1] and json.loads(runs[0])


def test_restore_rebuilds_the_same_layout():
    from plywood.core.optimize import restore

    parts, stocks = _job()
    settings = Settings(tries=100)
    r = optimize(parts, stocks, settings)
    back = restore(parts, stocks, settings, r.plan)
    assert check(back) == [] and back.plan == r.plan
    assert [lay.steps for lay in back.layouts] == [lay.steps for lay in r.layouts]


def test_restore_rejects_a_plan_that_no_longer_fits():
    from plywood.core.guillotine import PlanError
    from plywood.core.optimize import restore

    parts, stocks = _job()
    r = optimize(parts, stocks, fast())
    bigger = [part(p.name, p.length / IN + 10, p.width / IN, qty=p.qty) for p in parts]
    with pytest.raises(PlanError):
        restore(bigger, stocks, fast(), r.plan)


def test_browsing_one_sheet_keeps_the_others():
    from plywood.core.optimize import browse, choose

    parts, stocks = _job()
    settings = fast()
    r = optimize(parts, stocks, settings)
    assert len(r.layouts) >= 2
    pos, count = browse(parts, stocks, settings, r.plan)[0]
    assert pos == 0 and count > 2
    seen = {json.dumps(r.plan[0]["tree"])}
    plan = r.plan
    for k in range(1, 4):
        nxt = choose(parts, stocks, settings, plan, 1, k)
        assert check(nxt) == []
        assert [p["tree"] for p in nxt.plan[1:]] == [p["tree"] for p in r.plan[1:]]  # other sheets untouched
        assert sorted(p.label for p in nxt.layouts[0].placements) == sorted(p.label for p in r.layouts[0].placements)
        assert browse(parts, stocks, settings, nxt.plan)[0] == (k, count)
        seen.add(json.dumps(nxt.plan[0]["tree"]))
        plan = json.loads(json.dumps(nxt.plan))  # as saved by the browser
    assert len(seen) == 4
    back = choose(parts, stocks, settings, plan, 1, 0)
    assert back.plan[0]["tree"] == r.plan[0]["tree"]  # position 1 is always the optimizer's choice


def test_look_alike_layouts_are_shown_once():
    from plywood.core import optimize as O

    # Two big panels and interchangeable stretchers: lots of layouts that differ only in stretcher order.
    parts = [part("side", 32.5, 27, qty=2), part("stretcher", 23.75, 4, qty=2), part("rail", 23.75, 6, qty=2)]
    settings = fast()
    r = optimize(parts, [sheet()], settings)
    job = O.prepare(parts, [sheet()], settings)
    b = O._replay_all(job, r.plan)[0]
    trees, _ = O._options(job, b, r.plan[0])
    assert 1 < len(trees) <= len(O._ranked(job, b)) / 2
    looks = [O._look(O.replay(json.loads(t), 0, job.stocks, settings, job.instances), settings) for t in trees]
    assert not any(O._looks_same(looks[i], looks[j]) for i in range(len(looks)) for j in range(i))


# ---------------------------------------------------------------- lumber: planing, snipe, boards to find

BOARD = StockKind.BOARD


def board(length, width, thickness, qty=None, rough=False, name="board"):
    return Stock(name, length * IN, width * IN, thickness * IN, qty=qty, kind=BOARD, rough=rough)


def bpart(name, length, width, thickness, qty=1):
    return Part(name, length * IN, width * IN, thickness * IN, qty=qty, kind=BOARD)


def planes(r):
    return [(lay, st) for lay in r.layouts for st in lay.steps if st.direction == "plane"]


def test_board_at_most_max_planing_thicker():
    p = [bpart("rail", 30, 3, 0.75)]
    s = fast(default_boards=False)
    assert placed(optimize(p, [board(96, 6, 1.0)], s)) == 1  # 1/4" over: fine
    r = optimize(p, [board(96, 6, 1.25)], s)  # 1/2" over: too much
    assert placed(r) == 0 and "no board stock" in r.unplaced[0].reason


def test_rough_board_must_clean_up():
    p = [bpart("rail", 30, 3, 0.75)]
    s = fast(default_boards=False)
    assert placed(optimize(p, [board(96, 6, 0.75, rough=True)], s)) == 0  # nothing left to joint and plane
    r = optimize(p, [board(96, 6, 0.875, rough=True)], s)
    assert placed(r) == 1 and check(r) == []


def test_surfaced_board_at_part_thickness_needs_no_planing():
    r = optimize([bpart("rail", 30, 3, 0.75, qty=2)], [board(96, 3.5, 0.75)], fast())
    assert placed(r) == 2 and planes(r) == [] and check(r) == []


def test_exact_thickness_beats_planing():
    stocks = [board(96, 6, 1.0, qty=1, name="thick"), board(96, 6, 0.75, qty=1, name="exact")]
    r = optimize([bpart("rail", 30, 3, 0.75)], stocks, fast())
    assert [lay.stock.name for lay in r.layouts] == ["exact"] and planes(r) == []


def test_planed_segments_have_snipe_and_planer_length():
    s = fast()
    r = optimize([bpart("block", 6, 2, 0.75, qty=3), bpart("rail", 40, 3, 0.75)], [board(96, 6, 1.0, rough=True)], s)
    assert placed(r) == 4 and check(r) == []  # check() also verifies parts stay out of the snipe
    for _, st in planes(r):
        assert st.piece.w >= s.min_planer_length - 1e-6 and st.jointed


def test_board_steps_crosscut_then_plane_then_cut_up():
    r = optimize([bpart("rail", 30, 2.5, 0.75, qty=2), bpart("leg", 20, 1.5, 0.75, qty=2)],
                 [board(96, 6, 1.0, rough=True)], fast())
    assert check(r) == [] and planes(r)
    for lay, st in planes(r):
        steps = lay.steps
        i = steps.index(st)
        if st.piece_label:  # the segment was crosscut off the board earlier
            assert any(st.piece_label in [g.label for g in s.segments] for s in steps[:i])
        # the next step cuts the planed segment itself (the snipe comes off first)
        assert steps[i + 1].piece_label == st.piece_label and steps[i + 1].direction == "crosscut"


def test_boards_to_find_only_when_nothing_matches():
    p = [bpart("rail", 30, 3, 0.75, qty=2), bpart("leg", 28, 1.75, 1.75, qty=4)]
    r = optimize(p, [], fast())
    finds = {lay.stock.name for lay in r.layouts if lay.stock.find}
    assert finds == {"4/4 board", "8/4 board"} and check(r) == []
    r = optimize(p, [board(96, 6, 0.75), board(96, 4, 2.0)], fast())
    assert not any(lay.stock.find for lay in r.layouts) and placed(r) == 6 and check(r) == []


def test_wide_part_gets_a_wider_board_to_find():
    r = optimize([bpart("panel", 30, 9, 0.75)], [], fast())
    assert placed(r) == 1 and r.layouts[0].stock.width >= 9 * IN + fast().edge_joint


def test_board_plan_restores():
    from plywood.core.optimize import restore

    p = [bpart("rail", 30, 2.5, 0.75, qty=3), bpart("leg", 29, 1.5, 1.5, qty=4)]
    stocks = [board(80, 7, 1.0, qty=2, rough=True), board(70, 6, 1.75, qty=1, rough=True)]
    s = Settings(tries=50)
    r = optimize(p, stocks, s)
    back = restore(p, stocks, s, json.loads(json.dumps(r.plan)))
    assert check(back) == [] and back.plan == r.plan and [l.steps for l in back.layouts] == [l.steps for l in r.layouts]


@pytest.mark.parametrize("seed", range(15))
def test_random_board_jobs_are_valid(seed):
    rng = random.Random(seed)
    parts = [
        bpart(f"p{i}", rng.uniform(3, 50), rng.uniform(0.75, 5), rng.choice([0.75, 0.75, 1.0, 1.5]), qty=rng.randint(1, 3))
        for i in range(rng.randint(1, 10))
    ]
    stocks = [
        board(rng.uniform(30, 110), rng.uniform(3, 9), rng.choice([0.75, 1.0, 1.25, 1.75, 2.0]),
              qty=rng.randint(1, 2), rough=rng.random() < 0.5, name=f"b{i}")
        for i in range(rng.randint(0, 4))
    ]
    r = optimize(parts, stocks, Settings(tries=20, seed=seed))
    assert check(r) == []
    assert placed(r) + sum(u.count for u in r.unplaced) == sum(p.qty for p in parts)


def test_each_saw_uses_its_own_kerf():
    from plywood.core.optimize import restore

    s = Settings(tries=0, sheet_kerf=0.1 * IN, rip_kerf=0.125 * IN, crosscut_kerf=0.09 * IN, rough_crosscut_kerf=0.2 * IN)
    p = [bpart("rail", 30, 2.5, 0.75, qty=2), part("panel", 20, 20)]
    stocks = [board(96, 6, 1.0, rough=True), sheet()]
    r = optimize(p, stocks, s)
    assert check(r) == []
    for lay in r.layouts:
        kerfs = {(st.direction, round(st.kerf / IN, 3)) for st in lay.steps if st.direction in ("rip", "crosscut")}
        if lay.stock.kind == BOARD:
            assert ("crosscut", 0.2) in kerfs  # cutting the rough board into segments: jig saw
            assert ("crosscut", 0.09) in kerfs  # snipe and final crosscuts: miter saw
            assert all(k in (0.125,) for d, k in kerfs if d == "rip")
        else:
            assert {k for _, k in kerfs} == {0.1}
    back = restore(p, stocks, s, json.loads(json.dumps(r.plan)))
    assert [l.steps for l in back.layouts] == [l.steps for l in r.layouts]
