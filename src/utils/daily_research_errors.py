"""Typed failures for the resumable daily-research pipeline."""

from __future__ import annotations

from typing import Optional


class PaperStageError(RuntimeError):
    """A recoverable paper-processing failure with an explicit stage name.

    Persisting the stage separately is crucial: a translation outage must not
    discard a valid score/TL;DR, while a scoring-model validation failure must
    not be mislabeled merely because an error message happens to contain the
    word "translation".
    """

    VALID_STAGES = frozenset({"score", "translation", "analysis"})

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        cause: Optional[BaseException] = None,
    ):
        if stage not in self.VALID_STAGES:
            raise ValueError(f"未知论文处理阶段: {stage!r}")
        self.stage = stage
        self.cause = cause
        super().__init__(message)


def paper_stage_error(stage: str, exc: BaseException, context: str = "") -> PaperStageError:
    """Preserve an existing typed error or classify a lower-level exception."""
    if isinstance(exc, PaperStageError):
        return exc
    prefix = f"{context}: " if context else ""
    return PaperStageError(stage, f"{prefix}{exc}", cause=exc)
