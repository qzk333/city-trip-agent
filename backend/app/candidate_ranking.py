"""Bounded set selection; protected places never disappear behind a soft score."""

import re

from app.schemas import CandidateMatch, Place, TravelProfile
from app.tools import distance_meters
from app.travel_profile import THEME_NAMES, THEMES, normalize_profile


def matches_themes(place: Place, profile: TravelProfile) -> list[str]:
    text = place.name + " " + place.description
    return [
        t
        for t in profile.themes
        if t in place.traits.themes or t == place.category or any(word in text for word in THEMES[t])
    ]


def excluded(place: Place, profile: TravelProfile) -> bool:
    return any(word in place.name for word in profile.excluded_keywords)


def same_complex(a: Place, b: Place) -> bool:
    # This suppresses ordinary duplicate suggestions, never merges their stable IDs.
    def museum_name(name):
        return re.sub(r"(?:[东西南北]馆|主馆|分馆)$", "", name)

    return (
        "博物馆" in a.name
        and "博物馆" in b.name
        and museum_name(a.name) == museum_name(b.name)
        and a.coordinate_system == b.coordinate_system
        and distance_meters(a, b) <= 300
    )


def rank_candidates(catalog, profile, *, protected=(), limit=40):
    selected = [catalog[pid] for pid in dict.fromkeys(protected) if pid in catalog]
    remaining = [p for p in catalog.values() if p.id not in protected and not excluded(p, profile)]
    result = []
    for p in selected:
        result.append(
            p.model_copy(
                update={
                    "candidate_match": CandidateMatch(
                        score=10,
                        relevance=1,
                        diversity_penalty=0,
                        detour_penalty=0,
                        reasons=["保留你明确选择的地点"],
                    )
                }
            )
        )
    for _ in range(min(limit, len(remaining))):
        remaining = [p for p in remaining if not any(same_complex(p, q) for q in selected)]
        if not remaining:
            break
        scored = []
        for p in remaining:
            matched = matches_themes(p, profile)
            # Broad and fine synonyms do not stack, and selection order is not a hidden weight.
            relevance = max((0.35 if t in {"culture", "city"} else 0.7 for t in matched), default=0)
            overview = next(
                (e for e in p.traits.evidence if e.field == "retrieval" and e.value == "景点"), None
            )
            if overview:
                position = re.search(r"第 (\d+) 条", overview.excerpt)
                bonus = (0.08 if profile.themes else 0.24) / int(position[1]) if position else 0
                relevance = min(1, relevance + bonus)
            same = sum(x.category == p.category for x in selected)
            penalty = min(0.55, same * (0.22 if p.category == "coffee" else 0.09))
            if p.category == "coffee" and "coffee" not in profile.themes:
                penalty += 0.3
            # A secondary tie-breaker only: there is no meaningful detour before any stop is selected.
            distance = min(
                (distance_meters(p, q) for q in selected if p.coordinate_system == q.coordinate_system),
                default=0,
            )
            detour = min(0.2, distance / 100000)
            reasons = [f"匹配{THEME_NAMES[t]}" for t in matched]
            if overview:
                reasons.append("城市景点概览查询命中，供代表性行程比较（非热度排名）")
            if profile.walking == "less":
                reasons.append("优先集中安排；内部步行仍待确认")
            score = relevance - penalty - detour
            scored.append(
                (
                    score,
                    p.id,
                    p,
                    CandidateMatch(
                        score=round(score, 4),
                        relevance=relevance,
                        diversity_penalty=round(penalty, 4),
                        detour_penalty=round(detour, 4),
                        reasons=reasons[:6] or ["同城备选，旅行属性待核实"],
                    ),
                )
            )
        _, _, chosen, match = min(scored, key=lambda item: (-item[0], item[1]))
        result.append(chosen.model_copy(update={"candidate_match": match}))
        selected.append(chosen)
        remaining.remove(chosen)
    return {p.id: p for p in result}


def select_for_requirements(req, catalog, candidate_limit=None):
    profile = normalize_profile(req.profile, req.note)
    protected = list(
        dict.fromkeys([*req.must_visit, *req.place_days, *(a.place_id for a in req.appointments)])
    )
    ranked = rank_candidates(catalog, profile, protected=protected)
    # Short-list, with a separate bounded reserve. Never reintroduce the entire city directory.
    count = max(len(protected), candidate_limit or req.days * 8)
    return dict(list(ranked.items())[: max(count, 1)])
