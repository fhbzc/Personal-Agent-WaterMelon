"""WHETHER A REASON'S CONDITION COVERS A JOB OR A CASE - by the shared words.

A condition says, in each dimension, the terms it holds for (a term covers
everything under it), the terms it is EXCEPTED from, or nothing (any). A job
or a case has its terms in each dimension. Dimension by dimension:

  - "covered": every dimension the condition names holds, and no exception;
  - "near": all hold but one, and that one misses only by a sibling (the
    condition says slides, the job is a poster: both visual material);
  - "unknown": a dimension the condition names, that the job says nothing
    of - and nothing else fails;
  - "outside": a dimension fails outright, or an exception holds.

Code only, and loose by design: "near" and "unknown" go on to a model that
reads the condition's words and says whether it holds this time.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from .vocab import DIMENSIONS, Vocabulary


def _holds(tags: Sequence[str], allowed: Sequence[str], vocab: Vocabulary,
           terms: Dict[str, Dict[str, Any]]) -> str:
    """"yes" when a tag is at or under an allowed term, "near" when one is
    a sibling of one, else "no"."""
    best = "no"
    for tag in tags:
        for one in allowed:
            how = vocab.relation(tag, one, terms)
            if how in ("same", "under"):
                return "yes"
            if how == "sibling":
                best = "near"
    return best


def coverage(condition: Dict[str, Any], facets: Dict[str, Sequence[str]], vocab: Vocabulary,
             terms: Dict[str, Dict[str, Any]]) -> Tuple[str, List[str]]:
    """(covered | near | unknown | outside, what decided it)."""
    dims = condition.get("dims") if isinstance(condition.get("dims"), dict) else {}
    excluded = condition.get("excluded") if isinstance(condition.get("excluded"), dict) else {}
    why: List[str] = []
    near = unknown = 0
    for dim, values in excluded.items():
        tags = list(facets.get(dim) or [])
        if tags and values and _holds(tags, values, vocab, terms) == "yes":
            return "outside", [f"{dim}: excepted ({', '.join(values)})"]
    for dim in DIMENSIONS:
        allowed = [one for one in dims.get(dim) or [] if one]
        if not allowed:
            continue
        tags = list(facets.get(dim) or [])
        if not tags:
            unknown += 1
            why.append(f"{dim}: not known for this")
            continue
        how = _holds(tags, allowed, vocab, terms)
        if how == "yes":
            continue
        if how == "near":
            near += 1
            why.append(f"{dim}: {', '.join(tags)} is next to {', '.join(allowed)}")
            continue
        return "outside", [f"{dim}: {', '.join(tags)} is not {', '.join(allowed)}"]
    if near > 1:
        return "outside", why
    if near == 1:
        return "near", why
    if unknown:
        return "unknown", why
    return "covered", why


def closeness(one: Dict[str, Sequence[str]], other: Dict[str, Sequence[str]],
              vocab: Vocabulary, terms: Dict[str, Dict[str, Any]]) -> float:
    """How alike two cases (or a job and a case) are, by their terms: the
    deliverable counts most, then purpose and presentation, then audience,
    then the tools."""
    weights = {"deliverable": 4.0, "purpose": 2.0, "presentation": 2.0,
               "audience": 1.5, "environment": 0.5}
    score = 0.0
    for dim, weight in weights.items():
        a, b = list(one.get(dim) or []), list(other.get(dim) or [])
        if not a or not b:
            continue
        best = 0.0
        for x in a:
            for y in b:
                how = vocab.relation(x, y, terms)
                value = {"same": 1.0, "under": 0.75, "over": 0.75, "sibling": 0.5}.get(how, 0.0)
                best = max(best, value)
        score += weight * best
    return score


def fits_its_cases(condition: Dict[str, Any], case_facets: Sequence[Dict[str, Sequence[str]]],
                   vocab: Vocabulary, terms: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """The condition made to hold for every case it rests on: a dimension a
    case falls outside of is widened to the nearest term over all of them -
    or dropped (any) when there is none."""
    dims = dict(condition.get("dims") or {})
    for dim in list(dims):
        allowed = [one for one in dims.get(dim) or [] if vocab.resolve(one, terms)]
        if not allowed:
            dims.pop(dim, None)
            continue
        tags_each = [list(facets.get(dim) or []) for facets in case_facets]
        if all(not tags or _holds(tags, allowed, vocab, terms) == "yes" for tags in tags_each):
            dims[dim] = allowed
            continue
        everything = [tag for tags in tags_each for tag in tags] + allowed
        common = vocab.common(everything, terms)
        if common:
            dims[dim] = [common]
        else:
            dims.pop(dim, None)
    out = dict(condition)
    out["dims"] = dims
    return out
