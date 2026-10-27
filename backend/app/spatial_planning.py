"""Deterministic geography-aware day suggestions; no all-pairs provider calls."""

from app.lodging import boundary_window, day_boundaries
from app.schemas import HotelResearch
from app.tools import distance_meters


def near(a, b):
    return distance_meters(a, b) if a.coordinate_system == b.coordinate_system else 20000


def group_candidates(req, catalog, order):
    groups = [[] for _ in range(req.days)]
    pins = {**{a.place_id: a.day for a in req.appointments}, **req.place_days}
    for pid, day in pins.items():
        if pid in catalog:
            groups[day - 1].append(pid)
    capacities = []
    for day in range(1, req.days + 1):
        start, end = boundary_window(req, day, {})
        usable = max(0, end - start)
        cap = min(3 if req.pace == "relaxed" else 4, usable // 90)
        capacities.append(max(cap, len(groups[day - 1])))
    used = set(pins)
    pending = [pid for pid in dict.fromkeys([*req.must_visit, *order]) if pid in catalog and pid not in used]
    # Seed days with geographically separated, still relevant candidates.
    for index, group in enumerate(groups):
        if group or not capacities[index] or not pending:
            continue
        seeds = [catalog[pid] for row in groups for pid in row]
        start, end = day_boundaries(req, HotelResearch(), index + 1)
        anchor = start[1] or end[1]
        if anchor:
            seed = min(pending[: max(req.days * 3, 1)], key=lambda pid: near(catalog[pid], anchor))
        else:
            seed = max(
                pending[: max(req.days * 3, 1)],
                key=lambda pid: min((near(catalog[pid], p) for p in seeds), default=0),
            )
        group.append(seed)
        pending.remove(seed)
    while pending:
        # Choose a place/day pair jointly. Assigning every remaining high-ranked stop
        # sequentially can force a same-area spillover into a distant, nearly full day.
        required = [pid for pid in pending if pid in req.must_visit]
        eligible = required if required else pending
        choices = [i for i, g in enumerate(groups) if len(g) < capacities[i]]
        if not choices:
            if not required:
                break
            choices = [
                i
                for i in range(req.days)
                if boundary_window(req, i + 1, {})[1] > boundary_window(req, i + 1, {})[0]
            ] or [0]
        _, index, pid = min(
            (
                min((near(catalog[pid], catalog[q]) for q in groups[i]), default=0)
                + 2500 * pending.index(pid) / max(len(pending), 1)
                + 500 * len(groups[i]) / max(capacities[i], 1),
                i,
                pid,
            )
            for i in choices
            for pid in eligible
        )
        groups[index].append(pid)
        pending.remove(pid)
    # Cheapest insertion; fixed appointment times are still handled by the scheduler.
    for i, group in enumerate(groups):
        chain = []
        for pid in group:
            if not chain:
                chain.append(pid)
                continue
            costs = []
            for at in range(len(chain) + 1):
                left = catalog[chain[at - 1]] if at else None
                right = catalog[chain[at]] if at < len(chain) else None
                cost = (near(left, catalog[pid]) if left else 0) + (near(catalog[pid], right) if right else 0)
                if left and right:
                    cost -= near(left, right)
                costs.append((cost, at))
            chain.insert(min(costs)[1], pid)
        groups[i] = chain
    return groups
