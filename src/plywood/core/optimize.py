"""Search over packing heuristics and keep the best result.

The search is repeatable: the same parts, stock and settings always give the same layout.
"""

from __future__ import annotations

import itertools
import json
import random
from collections import Counter
from dataclasses import dataclass, replace
from typing import Iterator

from plywood.core.guillotine import (
    BIN_RULES,
    RECT_RULES,
    SPLIT_RULES,
    Bin,
    Instance,
    Packing,
    PlanError,
    _leaves,
    bin_plan,
    pack,
    pack_strips,
    replay,
    to_layout,
)
from plywood.core.matching import fits_stock, stock_matches, with_default_sheets
from plywood.core.models import Part, Result, Settings, Stock, Unplaced

SORTS = {
    "area": lambda p: (-(p.length * p.width), -max(p.length, p.width)),
    "long": lambda p: (-max(p.length, p.width), -min(p.length, p.width)),
    "short": lambda p: (-min(p.length, p.width), -max(p.length, p.width)),
    "perimeter": lambda p: (-(p.length + p.width), -max(p.length, p.width)),
}

ANOTHER_TRIES = 300  # random layouts tried when looking for another layout of one sheet
SAME_LOOK = 0.05  # layouts whose big leftovers differ by less than this much of the sheet count as one


@dataclass
class Job:
    """Parts expanded into one instance per piece, matched to the stock they can come from."""

    stocks: list[Stock]  # including any default sheets that were added
    instances: list[Instance]
    unplaced: list[Unplaced]  # parts with no stock to come from
    settings: Settings


def prepare(parts: list[Part], stocks: list[Stock], settings: Settings) -> Job:
    stocks = with_default_sheets(parts, stocks, settings)
    unplaced: list[Unplaced] = []
    instances: list[Instance] = []
    for part in parts:
        if part.qty <= 0:
            continue
        matches = [i for i, s in enumerate(stocks) if stock_matches(part, s, settings)]
        if not matches:
            unplaced.append(Unplaced(part, part.qty, f"no {part.kind} stock with matching thickness/tag"))
            continue
        a = settings.allowance
        rough = replace(part, length=part.length + a, width=part.width + a) if a else part
        allowed = frozenset(i for i in matches if fits_stock(rough, stocks[i], settings))
        if not allowed:
            unplaced.append(Unplaced(part, part.qty, "too large for matching stock (check grain lock)"))
            continue
        tag = (part.tag or "").strip().lower() or None
        source = part if a else None
        instances.extend(Instance(rough, c + 1, allowed, tag, source) for c in range(part.qty))
    return Job(stocks, instances, unplaced, settings)


def _stock_orders(stocks: list[Stock]) -> list[list[int]]:
    """On-hand pieces always come before purchased stock; vary the on-hand order."""
    on_hand = [i for i, s in enumerate(stocks) if s.on_hand]
    buy = sorted(
        (i for i, s in enumerate(stocks) if not s.on_hand),
        key=lambda i: -stocks[i].length * stocks[i].width,
    )
    area = lambda i: stocks[i].length * stocks[i].width  # noqa: E731
    smallest_first = sorted(on_hand, key=area) + buy
    largest_first = sorted(on_hand, key=area, reverse=True) + buy
    return [smallest_first] if smallest_first == largest_first else [smallest_first, largest_first]


def _candidates(
    instances: list[Instance], stocks: list[Stock], stock_orders: list[list[int]], settings: Settings, tries: int
) -> Iterator[Packing]:
    """Strip layouts, then every heuristic combination, then `tries` random variations."""

    def strip_key(it: Instance):
        p = it.part
        free = p.grain == "none"
        width = min(p.length, p.width) if free else (p.width if p.grain == "length" else p.length)
        return (-width, -(p.length * p.width))

    # Shop-style strip layouts first: cheap, and often as good as anything else.
    for stock_order in stock_orders:
        for exact_only in (False, True):
            yield pack_strips(sorted(instances, key=strip_key), stocks, stock_order, settings, exact_only)
    for sort_name, rect_rule, split_rule, bin_rule, stock_order in itertools.product(
        SORTS, RECT_RULES, SPLIT_RULES, BIN_RULES, stock_orders
    ):
        order = sorted(instances, key=lambda it: SORTS[sort_name](it.part))
        yield pack(order, stocks, stock_order, settings, rect_rule, split_rule, bin_rule)

    # Random restarts: noisy sort keys, random heuristics, shuffled on-hand order.
    # Everything here iterates in a fixed order so a given seed always gives the same result.
    rng = random.Random(settings.seed)
    unique = list(dict.fromkeys(it.part for it in instances))
    for _ in range(tries if instances else 0):
        sort_key = SORTS[rng.choice(list(SORTS))]
        noise = {p: rng.uniform(0.75, 1.25) for p in unique}
        order = sorted(instances, key=lambda it: tuple(v * noise[it.part] for v in sort_key(it.part)))
        stock_order = list(rng.choice(stock_orders))
        n_on_hand = sum(1 for i in stock_order if stocks[i].on_hand)
        head = stock_order[:n_on_hand]
        rng.shuffle(head)
        stock_order[:n_on_hand] = head
        if rng.random() < 0.3:
            noisy = sorted(instances, key=lambda it: (-min(it.part.length, it.part.width) * noise[it.part], -it.part.length))
            yield pack_strips(noisy, stocks, stock_order, settings, rng.random() < 0.5)
            continue
        yield pack(
            order,
            stocks,
            stock_order,
            settings,
            rng.choice(RECT_RULES),
            rng.choice(SPLIT_RULES),
            rng.choice(BIN_RULES),
        )


def optimize(parts: list[Part], stocks: list[Stock], settings: Settings | None = None) -> Result:
    settings = settings or Settings()
    job = prepare(parts, stocks, settings)
    best: Packing | None = None
    best_score = None
    iterations = 0
    for packing in _candidates(job.instances, job.stocks, _stock_orders(job.stocks), settings, settings.tries):
        iterations += 1
        score = packing.score(settings.priority)
        if best_score is None or score < best_score:
            best, best_score = packing, score
    bins = best.bins if best is not None else []
    # On-hand first, then purchased; biggest stock first within each.
    bins = sorted(bins, key=lambda b: (not b.stock.on_hand, -b.stock.length * b.stock.width, b.index))
    return finish(job, bins, iterations)


def finish(job: Job, bins: list[Bin], iterations: int = 0, bases: list | None = None) -> Result:
    """Turn packed bins (in sheet order) into a result, with the plan to restore it later.

    `bases`: each sheet's layout as the optimizer first chose it (default: the current one).
    """
    settings = job.settings
    index = {id(it): i for i, it in enumerate(job.instances)}
    layouts = [to_layout(b, n + 1, settings) for n, b in enumerate(bins)]
    placed = {id(leaf.item) for b in bins for leaf in _leaves(b.root) if leaf.kind == "part"}
    leftover = Counter(it.final for it in job.instances if id(it) not in placed)
    unplaced = job.unplaced + [Unplaced(p, n, "ran out of on-hand stock") for p, n in leftover.items()]
    plan = []
    for n, b in enumerate(bins):
        tree = bin_plan(b, index)
        base = bases[n] if bases and bases[n] is not None else tree
        plan.append({"stock": b.stock_id, "tree": tree, "base": base})
    return Result(layouts=layouts, unplaced=unplaced, settings=settings, stocks=job.stocks, iterations=iterations, plan=plan)


def _replay_all(job: Job, plan: list[dict]) -> list[Bin]:
    try:
        bins = [replay(p["tree"], int(p["stock"]), job.stocks, job.settings, job.instances, n) for n, p in enumerate(plan)]
    except PlanError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise PlanError(f"unreadable plan: {e}") from None
    items = [id(leaf.item) for b in bins for leaf in _leaves(b.root) if leaf.kind == "part"]
    if len(items) != len(set(items)):
        raise PlanError("a part is used twice")
    return bins


def restore(parts: list[Part], stocks: list[Stock], settings: Settings, plan: list[dict]) -> Result:
    """Rebuild a saved result. Raises PlanError if the plan no longer fits the inputs."""
    job = prepare(parts, stocks, settings)
    return finish(job, _replay_all(job, plan), bases=[p.get("base") for p in plan])


# ---------------------------------------------------------------- other layouts of one sheet


def _canon(tree) -> str:
    """A tree as a comparable string (JSON from the browser turns 762.0 into 762)."""

    def walk(t):
        if t is None:
            return None
        if isinstance(t, dict):
            return {"i": int(t["i"]), "r": bool(t["r"])}
        return [t[0], float(t[1]), *(walk(c) for c in t[2:])]

    return json.dumps(walk(tree))


def _look(b: Bin, settings: Settings) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Where a sheet's big leftovers are, and where its parts are, as coarse bitmaps (one int per row).

    Big leftovers: usable offcuts at least a quarter the size of the largest one. Two layouts
    that leave those in the same place look the same however the parts are shuffled around.
    """
    cell = max(b.stock.length, b.stock.width) / 192
    n_rows = int(b.stock.width / cell) + 2
    leaves = list(_leaves(b.root))
    frees = [n for n in leaves if n.kind == "free" and min(n.w, n.h) >= settings.min_offcut]
    biggest = max((n.w * n.h for n in frees), default=0.0)

    def bitmap(rects) -> tuple[int, ...]:
        rows = [0] * n_rows
        for x, y, w, h in rects:
            x0, x1 = round(x / cell), round((x + w) / cell)
            mask = ((1 << max(x1 - x0, 0)) - 1) << x0
            for r in range(round(y / cell), min(round((y + h) / cell), n_rows)):
                rows[r] |= mask
        return tuple(rows)

    left = bitmap((n.x, n.y, n.w, n.h) for n in frees if n.w * n.h >= biggest / 4)
    parts = bitmap((n.x, n.y, n.pw, n.ph) for n in leaves if n.kind == "part")
    return left, parts


def _looks_same(a, b) -> bool:
    which = 0 if any(a[0]) or any(b[0]) else 1  # no usable leftover on either: compare the parts
    cells = len(a[which]) * 192
    return sum((x ^ y).bit_count() for x, y in zip(a[which], b[which])) < SAME_LOOK * cells


_ranked_cache: dict[tuple, list[tuple[str, tuple]]] = {}


def _ranked(job: Job, b: Bin) -> list[tuple[str, tuple]]:
    """Every distinct layout found for this sheet's parts on its stock piece, best first, with its look."""
    items = [leaf.item for leaf in _leaves(b.root) if leaf.kind == "part"]
    index = {id(it): i for i, it in enumerate(job.instances)}
    key = (
        repr(job.settings),
        b.stock_id,
        job.stocks[b.stock_id],
        tuple(sorted((index[id(it)], it.part, it.copy, it.source) for it in items)),
    )
    if key in _ranked_cache:
        return _ranked_cache[key]
    items.sort(key=lambda it: index[id(it)])
    found: dict[str, tuple] = {}
    for packing in _candidates(items, job.stocks, [[b.stock_id]], job.settings, ANOTHER_TRIES):
        if len(packing.bins) != 1 or packing.unplaced:
            continue
        tree = _canon(bin_plan(packing.bins[0], index))
        if tree not in found:
            found[tree] = (packing.score(job.settings.priority), _look(packing.bins[0], job.settings))
    ranked = [(t, found[t][1]) for t in sorted(found, key=lambda t: (found[t][0], t))]
    if len(_ranked_cache) > 256:
        _ranked_cache.clear()
    _ranked_cache[key] = ranked
    return ranked


def _options(job: Job, b: Bin, entry: dict) -> tuple[list[str], int]:
    """A sheet's layouts in browsing order and the current position.

    The optimizer's choice comes first, then the best of each group of look-alikes (see `_look`).
    """
    base_tree = entry.get("base") or entry["tree"]
    base = _canon(base_tree)
    current = _canon(entry["tree"])
    base_bin = replay(json.loads(base), b.stock_id, job.stocks, job.settings, job.instances)
    kept = [(base, _look(base_bin, job.settings))]
    for tree, look in _ranked(job, b):
        if tree != base and not any(_looks_same(look, k[1]) for k in kept):
            kept.append((tree, look))
    trees = [t for t, _ in kept]
    if current not in trees:
        trees.insert(1, current)  # a look-alike picked before this filtering existed
    return trees, trees.index(current)


def browse(parts: list[Part], stocks: list[Stock], settings: Settings, plan: list[dict]) -> list[tuple[int, int]]:
    """For each sheet: (position of the current layout, how many layouts there are)."""
    job = prepare(parts, stocks, settings)
    bins = _replay_all(job, plan)
    out = []
    for b, entry in zip(bins, plan):
        trees, pos = _options(job, b, entry)
        out.append((pos, len(trees)))
    return out


def choose(parts: list[Part], stocks: list[Stock], settings: Settings, plan: list[dict], number: int, position: int) -> Result:
    """The same result with sheet `number` switched to its layout at `position` (see `browse`)."""
    job = prepare(parts, stocks, settings)
    bins = _replay_all(job, plan)
    b = bins[number - 1]
    trees, _ = _options(job, b, plan[number - 1])
    tree = json.loads(trees[max(0, min(position, len(trees) - 1))])
    bins[number - 1] = replay(tree, b.stock_id, job.stocks, settings, job.instances, b.index)
    return finish(job, bins, bases=[p.get("base") for p in plan])
