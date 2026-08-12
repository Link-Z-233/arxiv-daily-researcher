import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sources.base_source import PaperMetadata, normalize_arxiv_identifier  # noqa: E402
from sources.search_agent import SearchAgent  # noqa: E402
from sources.semantic_scholar_enricher import SemanticScholarEnricher  # noqa: E402


class _Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _journal_paper() -> PaperMetadata:
    return PaperMetadata(
        paper_id="10.1000/example",
        title="Example",
        authors=["Author"],
        abstract="Abstract",
        published_date=datetime.now(timezone.utc),
        url="https://doi.org/10.1000/example",
        source="prl",
        doi="10.1000/example",
    )


class SemanticScholarBoundaryTests(unittest.TestCase):
    def test_arxiv_identifier_validation_accepts_real_ids_only(self):
        self.assertEqual(normalize_arxiv_identifier(" 2501.12345v2 "), "2501.12345v2")
        self.assertEqual(normalize_arxiv_identifier("hep-th/9901001v3"), "hep-th/9901001v3")
        for invalid in (
            None,
            123,
            "arXiv:2501.12345",
            "2501.12345/../../internal",
            "2501.12345v0",
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(normalize_arxiv_identifier(invalid))

    def test_invalid_external_id_keeps_valid_tldr_but_never_makes_a_url(self):
        enricher = SemanticScholarEnricher()
        self.addCleanup(enricher.close)
        enricher._api_get = lambda *_args, **_kwargs: _Response(
            {
                "tldr": {"text": "  A valid summary.  "},
                "externalIds": {"ArXiv": "2501.12345/../../internal"},
            }
        )

        info = enricher.get_paper_info("10.1000/example")

        self.assertEqual(info, {"tldr": "A valid summary."})
        self.assertIsNone(enricher.get_arxiv_id("\ninvalid"))

    def test_paper_metadata_only_constructs_https_pdf_for_valid_arxiv_id(self):
        paper = _journal_paper()
        paper.arxiv_id = "2501.12345v2"
        self.assertEqual(
            paper.get_arxiv_pdf_url(), "https://arxiv.org/pdf/2501.12345v2.pdf"
        )
        paper.arxiv_id = "2501.12345/../../internal"
        self.assertFalse(paper.has_pdf_access())
        self.assertIsNone(paper.get_arxiv_pdf_url())

    def test_search_agent_defensively_rejects_invalid_custom_enrichment(self):
        paper = _journal_paper()
        agent = SearchAgent.__new__(SearchAgent)
        agent.semantic_scholar_enricher = SimpleNamespace(
            get_paper_info=lambda _doi: {
                "tldr": "Provider TLDR",
                "arxiv_id": "2501.12345/../../internal",
            }
        )

        enriched = agent._enrich_with_semantic_scholar([paper])

        self.assertEqual(enriched[0].semantic_scholar_tldr, "Provider TLDR")
        self.assertIsNone(enriched[0].arxiv_id)
        self.assertIsNone(enriched[0].pdf_url)


if __name__ == "__main__":
    unittest.main()
