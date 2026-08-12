"""Deterministic cache keys for resumable daily-paper processing.

Persisted LLM output is useful only while the exact input and the relevant
configuration are unchanged.  These helpers deliberately never include API
keys or other credentials; they describe reproducible processing inputs, not
secrets.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping

from config import settings


def _canonical_json(value: Any) -> str:
    """Serialize JSON-compatible input in one stable representation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def stage_input_fingerprint(value: Any) -> str:
    """Return a versioned SHA-256 key for one processing-stage input."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _model_settings(model: Any, temperature: Any) -> Dict[str, Any]:
    """Keep only non-secret model settings that affect generated output."""
    return {
        "model_name": str(getattr(model, "model_name", "")),
        "temperature": temperature,
    }


def build_stage_input_fingerprints(
    paper: Any,
    keywords: Mapping[str, Any],
    deep_template: Mapping[str, Any],
) -> Dict[str, str]:
    """Build score, translation and deep-analysis cache keys for one paper.

    The explicit prompt revisions are intentional.  When code changes a
    prompt without adding a new configuration field, incrementing its revision
    invalidates only that stage's incomplete cached output.
    """
    paper_for_score = {
        "source": str(getattr(paper, "source", "")),
        "paper_id": str(getattr(paper, "paper_id", "")),
        "title": str(getattr(paper, "title", "")),
        "authors": [str(author) for author in getattr(paper, "authors", [])],
        "abstract": str(getattr(paper, "abstract", "")),
    }
    score_payload = {
        "schema": "daily-research-score-input-v1",
        "paper": paper_for_score,
        # Preserve configured order as it also defines the prompt's order.
        "keywords": [[str(key), value] for key, value in keywords.items()],
        "research_context": str(settings.RESEARCH_CONTEXT),
        "score_settings": {
            "max_score_per_keyword": settings.MAX_SCORE_PER_KEYWORD,
            "enable_author_bonus": settings.ENABLE_AUTHOR_BONUS,
            "expert_authors": [str(author) for author in settings.EXPERT_AUTHORS],
            "author_bonus_points": settings.AUTHOR_BONUS_POINTS,
            "passing_score_base": settings.PASSING_SCORE_BASE,
            "passing_score_weight_coefficient": settings.PASSING_SCORE_WEIGHT_COEFFICIENT,
        },
        "model": _model_settings(settings.CHEAP_LLM, settings.CHEAP_LLM.temperature),
        "prompt_revision": "daily-keyword-score-v2",
    }

    translation_payload = {
        "schema": "daily-research-translation-input-v1",
        "abstract": paper_for_score["abstract"],
        # _call_cheap_llm_plain intentionally uses this fixed temperature.
        "model": _model_settings(settings.CHEAP_LLM, 0.3),
        "prompt_revision": "daily-abstract-translation-v1",
    }

    analysis_payload = {
        "schema": "daily-research-analysis-input-v1",
        "paper": {
            "source": paper_for_score["source"],
            "paper_id": paper_for_score["paper_id"],
            "title": paper_for_score["title"],
            "abstract": paper_for_score["abstract"],
            "pdf_url": getattr(paper, "pdf_url", None),
            "arxiv_id": getattr(paper, "arxiv_id", None),
            "updated_date": (
                getattr(paper, "updated_date", None).isoformat()
                if getattr(paper, "updated_date", None) is not None
                else None
            ),
        },
        "research_context": str(settings.RESEARCH_CONTEXT),
        "pdf_parser": {
            "mode": settings.PDF_PARSER_MODE,
            "mineru_model_version": settings.MINERU_MODEL_VERSION,
            "fallback_to_abstract": True,
            "content_limit_characters": 15000,
            "pymupdf_page_limit": 20,
        },
        "model": _model_settings(settings.SMART_LLM, settings.SMART_LLM.temperature),
        "template": dict(deep_template),
        "prompt_revision": "daily-deep-analysis-v1",
    }

    return {
        "score": stage_input_fingerprint(score_payload),
        "translation": stage_input_fingerprint(translation_payload),
        "analysis": stage_input_fingerprint(analysis_payload),
    }
