"""Small, shared contract for trustworthy deep-analysis provenance.

The daily pipeline may fall back from a parsed PDF to the abstract.  That is
useful for ordinary analysis, but a conclusion labelled as a *full-text* TL;DR
must only be shown when it was actually based on parsed PDF text.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


ANALYSIS_META_KEY = "__meta"
CONTENT_SOURCE_KEY = "content_source"
CONTENT_SOURCE_PDF = "pdf"
CONTENT_SOURCE_ABSTRACT_FALLBACK = "abstract_fallback"
FULL_TEXT_TLDR_FIELD = "full_text_tldr"


def analysis_content_source(analysis: Any) -> Optional[str]:
    """Return a recognized, code-written analysis content source, if present."""
    if not isinstance(analysis, Mapping):
        return None
    metadata = analysis.get(ANALYSIS_META_KEY)
    if not isinstance(metadata, Mapping):
        return None
    source = metadata.get(CONTENT_SOURCE_KEY)
    if source in {CONTENT_SOURCE_PDF, CONTENT_SOURCE_ABSTRACT_FALLBACK}:
        return source
    return None


def is_pdf_grounded_analysis(analysis: Any) -> bool:
    """Whether an analysis has explicit evidence that it used parsed PDF text."""
    return analysis_content_source(analysis) == CONTENT_SOURCE_PDF


def analysis_source_label(analysis: Any) -> Optional[str]:
    """Return a concise human-facing provenance label for new analysis records."""
    source = analysis_content_source(analysis)
    if source == CONTENT_SOURCE_PDF:
        return "PDF 全文"
    if source == CONTENT_SOURCE_ABSTRACT_FALLBACK:
        return "摘要降级"
    return None
