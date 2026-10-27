"""Resolve sourced guide place names through the shared city-restricted map gateway."""

import re
from time import monotonic

from app.agent_contracts import GuidePlaceResolution
from app.candidate_discovery import merge_query
from app.candidate_ranking import excluded, select_for_requirements
from app.schemas import AttributeEvidence
from app.tools import ToolUnavailable
from app.travel_profile import normalize_profile

MAX_GUIDE_QUERIES = 3


def place_name(value, city=""):
    value = re.sub(r"[\s·•,，。:：()（）\-－]", "", value).casefold()
    city = city.rstrip("市").casefold()
    if city and value.startswith(city):
        value = value[len(city) :]
        if value.startswith("市"):
            value = value[1:]
    return re.sub(r"(?:风景名胜区|风景区|景区)$", "", value)


def expand_guide_candidates(req, result, tools, catalog):
    result = result.model_copy(deep=True)
    pool = dict(catalog)
    if result.brief.status != "ready" or not result.brief.place_suggestions:
        return pool, result
    sources = {s.id: s for s in result.research.sources}
    profile = normalize_profile(req.profile, req.note)
    added, resolutions, seen = [], [], set()
    queries = 0
    for suggestion in result.brief.place_suggestions:
        name = place_name(suggestion.name, req.city)
        if name in seen:
            continue
        seen.add(name)
        resolution = GuidePlaceResolution(
            name=suggestion.name, status="unsupported", source_ids=suggestion.source_ids
        )
        texts = [
            sources[sid].title + " " + sources[sid].excerpt for sid in suggestion.source_ids if sid in sources
        ]
        if not texts or not any(name and name in place_name(text) for text in texts):
            resolution.note = "名称未出现在引用的已取得资料中，不为无依据的新地名调用地图。"
        elif any(word in suggestion.name for word in profile.excluded_keywords):
            resolution.status, resolution.note = "excluded", "与用户明确排除的关键词冲突。"
        else:
            matches = [p for p in pool.values() if place_name(p.name, req.city) == name]
            if len(matches) > 1:
                resolution.status, resolution.note = "ambiguous", "目录中有多个同名地点，未自动选择。"
            elif len(matches) == 1:
                if excluded(matches[0], profile):
                    resolution.status, resolution.note = "excluded", "已有地点与用户明确排除条件冲突。"
                else:
                    resolution.status, resolution.place_ids = "existing", [matches[0].id]
            elif (
                tools.settings.tool_provider != "amap"
                or queries >= MAX_GUIDE_QUERIES
                or tools.deadline - monotonic() < 25
            ):
                resolution.status, resolution.note = (
                    "skipped",
                    "演示模式、补查次数或剩余时间限制；保留线索，不编造地点。",
                )
            else:
                queries += 1
                try:
                    found = tools.search_candidates(suggestion.name, suggestion.category, limit=3)
                    matches = [
                        p
                        for p in found.values()
                        if place_name(p.name, req.city) == name and not excluded(p, profile)
                    ]
                    if len(matches) == 1:
                        p = pool.get(matches[0].id, matches[0])
                        merge_query(pool, {p.id: p}, suggestion.name, req.city)
                        resolution.status, resolution.place_ids = (
                            ("existing" if p.id in catalog else "added"),
                            [p.id],
                        )
                        if p.id not in catalog:
                            added.append(p.id)
                    else:
                        resolution.status = "ambiguous" if len(matches) > 1 else "no_result"
                        resolution.note = "同城地图查询未得到唯一匹配，不将相似地名当成已确认地点。"
                except ToolUnavailable:
                    resolution.status, resolution.note = "unavailable", "地图补查暂不可用，继续使用原有候选。"
            for pid in resolution.place_ids:
                p = pool[pid]
                evidence = [
                    *p.traits.evidence,
                    AttributeEvidence(
                        field="guide_reference",
                        value=suggestion.name,
                        kind="observed",
                        source_url=sources[suggestion.source_ids[0]].url,
                        observed_at=result.research.fetched_at,
                        excerpt="攻略提供地点线索；同城地图核对身份，不证明营业、预约或建议适合程度。",
                    ),
                ]
                pool[pid] = p.model_copy(
                    update={"traits": p.traits.model_copy(update={"evidence": evidence[-12:]})}
                )
        resolutions.append(resolution)
    result.brief.place_resolutions = resolutions
    result.research.discovered_place_ids = added
    result.research.discovery_note = (
        f"攻略提出 {len(resolutions)} 个地点线索，地图补充 {len(added)} 个候选；"
        "同名或未确认地点保留为待确认，不自动排入行程。"
    )
    result.trace.append(
        {
            "tool": "guide_place_discovery",
            "status": "ok" if added else "no_additions",
            "queries": queries,
            "added_place_ids": added,
            "resolutions": [r.model_dump() for r in resolutions],
        }
    )
    return pool, result


def admit_guide_candidates(req, catalog, brief, *, limit=40):
    selected = select_for_requirements(req, catalog, candidate_limit=limit)
    guide_ids = {pid for r in brief.place_resolutions for pid in r.place_ids}
    protected = {*req.must_visit, *req.place_days, *(a.place_id for a in req.appointments), *guide_ids}
    capacity = max(limit, len(protected))
    for pid in dict.fromkeys(pid for r in brief.place_resolutions for pid in r.place_ids):
        if pid in selected or pid not in catalog:
            continue
        if len(selected) >= capacity:
            victim = next((p for p in reversed(selected) if p not in protected), None)
            if victim is None:
                continue
            selected.pop(victim)
        selected[pid] = catalog[pid]
    return selected
