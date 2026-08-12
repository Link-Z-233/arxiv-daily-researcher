"""Small, dependency-free helpers shared by scoring, reports and delivery.

The daily pipeline stores score responses for a long time.  These helpers keep
new V2 fields optional at every read boundary, so historical
``legacy_weighted_keyword_v1`` JSON can still be rendered, sorted and reviewed
without a data migration.
"""

from __future__ import annotations

import math
from typing import Any, Optional


LEGACY_WEIGHTED_KEYWORD_V1 = "legacy_weighted_keyword_v1"
CORE_RELEVANCE_V2 = "core_relevance_v2"
SUPPORTED_SCORE_STRATEGIES = frozenset(
    {LEGACY_WEIGHTED_KEYWORD_V1, CORE_RELEVANCE_V2}
)


def finite_score_or_none(value: Any) -> Optional[float]:
    """Return a finite numeric score, treating absent/invalid values as absent."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def response_strategy_id(response: Any) -> str:
    """Return a persisted strategy id, defaulting legacy rows safely."""
    value = getattr(response, "strategy_id", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return LEGACY_WEIGHTED_KEYWORD_V1


def uses_core_relevance_v2(response: Any) -> bool:
    """Whether a response has V2 qualification semantics."""
    return response_strategy_id(response) == CORE_RELEVANCE_V2


def ranking_score_for(response: Any) -> float:
    """Return the stable sort key for a score response.

    New responses provide ``ranking_score`` explicitly.  Old SQLite rows and
    third-party renderer fixtures only have ``total_score``; retaining that
    fallback is essential for backwards-compatible history hydration.
    """
    ranking = finite_score_or_none(getattr(response, "ranking_score", None))
    if ranking is not None:
        return ranking
    legacy_total = finite_score_or_none(getattr(response, "total_score", None))
    return legacy_total if legacy_total is not None else 0.0


def qualification_score_for(response: Any) -> float:
    """Return the score that controls a relevance decision.

    In V2 this is content-only ``relevance_score``.  Legacy history did not
    persist such a field, so its original total remains the correct fallback.
    """
    relevance = finite_score_or_none(getattr(response, "relevance_score", None))
    if relevance is not None:
        return relevance
    legacy_total = finite_score_or_none(getattr(response, "total_score", None))
    return legacy_total if legacy_total is not None else 0.0


def optional_score_value(response: Any, field_name: str) -> Optional[float]:
    """Read an optional finite response field without conflating ``None`` and 0."""
    return finite_score_or_none(getattr(response, field_name, None))


def qualification_threshold_for(response: Any) -> float:
    """Return the persisted qualification threshold with legacy fallback."""
    threshold = finite_score_or_none(getattr(response, "qualification_threshold", None))
    if threshold is not None:
        return threshold
    passing = finite_score_or_none(getattr(response, "passing_score", None))
    return passing if passing is not None else 0.0
