"""Search over packing heuristics and keep the best result."""

from __future__ import annotations

import itertools
import random
import time
from collections import Counter

from plywood.core.guillotine import BIN_RULES, RECT_RULES, SPLIT_RULES, Instance, Packing, pack, to_layout
from plywood.core.matching import fits_stock, stock_matches, with_default_sheets
from plywood.core.models import Part, Result, Settings, Stock, Unplaced

SORTS = {
    "area": lambda p: (-(p.length * p.width), -max(p.length, p.width)),
    "long": lambda p: (-max(p.length, p.width), -min(p.length, p.width)),
    "short": lambda p: (-min(p.length, p.width), -max(p.length, p.width)),
    "perimeter": lambda p: (-(p.length + p.width), -max(p.length, p.width)),
}


def _stock_orders(stocks: list[Stock]) -> list[list[int]]:
    """On-hand pieces always come before purchased stock; vary the on-hand order."""
    on_hand = [i for i, s in enumerate(stocks) if s.on_hand]
    buy = sorted(
        (i for i, s in enumerate(stocks) if not s.on_hand),
        key=lambda i: (stocks[i].cost / (stocks[i].length * stocks[i].width), -stocks[i].length * stocks[i].width),
    )
    area = lambda i: stocks[i].length * stocks[i].width  # noqa: E731
    smallest_first = sorted(on_hand, key=area) + buy
    largest_first = sorted(on_hand, key=area, reverse=True) + buy
    return [smallest_first] if smallest_first == largest_first else [smallest_first, largest_first]


def optimize(parts: list[Part], stocks: list[Stock], settings: Settings | None = None) -> Result:
    settings = settings or Settings()
    stocks = with_default_sheets(parts, stocks, settings)
    rng = random.Random(settings.seed)
    deadline = time.monotonic() + settings.time_budget

    unplaced: list[Unplaced] = []
    instances: list[Instance] = []
    for part in parts:
        if part.qty <= 0:
            continue
        matches = [i for i, s in enumerate(stocks) if stock_matches(part, s, settings)]
        if not matches:
            unplaced.append(Unplaced(part, part.qty, f"no {part.kind} stock with matching thickness/tag"))
            continue
        allowed = frozenset(i for i in matches if fits_stock(part, stocks[i], settings))
        if not allowed:
            unplaced.append(Unplaced(part, part.qty, "too large for matching stock (check grain lock)"))
            continue
        instances.extend(Instance(part, c + 1, allowed) for c in range(part.qty))

    best: Packing | None = None
    best_score = None
    iterations = 0

    def consider(packing: Packing) -> None:
        nonlocal best, best_score, iterations
        iterations += 1
        score = packing.score()
        if best_score is None or score < best_score:
            best, best_score = packing, score

    stock_orders = _stock_orders(stocks)
    # Deterministic sweep: every combination of sort, rect rule, split rule, bin rule, stock order.
    for sort_name, rect_rule, split_rule, bin_rule, stock_order in itertools.product(
        SORTS, RECT_RULES, SPLIT_RULES, BIN_RULES, stock_orders
    ):
        if best is not None and time.monotonic() > deadline:
            break
        order = sorted(instances, key=lambda it: SORTS[sort_name](it.part))
        consider(pack(order, stocks, stock_order, settings, rect_rule, split_rule, bin_rule))

    # Random restarts: noisy sort keys, random heuristics, shuffled on-hand order.
    random_runs = 0
    while time.monotonic() < deadline and instances:
        if settings.random_iterations is not None and random_runs >= settings.random_iterations:
            break
        random_runs += 1
        sort_key = SORTS[rng.choice(list(SORTS))]
        noise = {id(p): rng.uniform(0.75, 1.25) for p in {it.part for it in instances}}
        order = sorted(instances, key=lambda it: tuple(v * noise[id(it.part)] for v in sort_key(it.part)))
        stock_order = list(rng.choice(stock_orders))
        n_on_hand = sum(1 for i in stock_order if stocks[i].on_hand)
        head = stock_order[:n_on_hand]
        rng.shuffle(head)
        stock_order[:n_on_hand] = head
        consider(
            pack(
                order,
                stocks,
                stock_order,
                settings,
                rng.choice(RECT_RULES),
                rng.choice(SPLIT_RULES),
                rng.choice(BIN_RULES),
            )
        )

    layouts = []
    if best is not None:
        # On-hand first, then purchased; biggest stock first within each.
        ordered = sorted(best.bins, key=lambda b: (not b.stock.on_hand, -b.stock.length * b.stock.width, b.index))
        layouts = [to_layout(b, n + 1, settings) for n, b in enumerate(ordered)]
        leftover = Counter(it.part for it in best.unplaced)
        unplaced += [Unplaced(p, n, "ran out of on-hand stock") for p, n in leftover.items()]

    return Result(layouts=layouts, unplaced=unplaced, settings=settings, stocks=stocks, iterations=iterations)
