import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.analysis_agent import (  # noqa: E402
    AnalysisAgent,
    LLMResponseError,
    validate_deep_analysis_payload,
)
from config import settings  # noqa: E402


def _chat_response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5),
    )


class LLMResponseCompatibilityTests(unittest.TestCase):
    def test_chat_content_arrays_and_reasoning_content_are_normalized(self):
        response = _chat_response(
            [{"type": "text", "text": "first"}, SimpleNamespace(text="second")]
        )
        self.assertEqual(AnalysisAgent._extract_chat_text(response), "first\nsecond")

        reasoning_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, reasoning_content="fallback")
                )
            ]
        )
        self.assertEqual(AnalysisAgent._extract_chat_text(reasoning_response), "fallback")
        self.assertEqual(
            AnalysisAgent._extract_chat_text(
                {"choices": [{"message": {"content": {"text": "dict"}}}]}
            ),
            "dict",
        )

    def test_responses_output_shapes_are_normalized(self):
        response = SimpleNamespace(
            output=[
                SimpleNamespace(content=[SimpleNamespace(text="one")]),
                {"content": [{"type": "output_text", "text": "two"}]},
            ]
        )
        self.assertEqual(AnalysisAgent._extract_responses_text(response), "one\ntwo")
        self.assertEqual(
            AnalysisAgent._extract_responses_text(SimpleNamespace(output_text="direct")),
            "direct",
        )
        self.assertEqual(
            AnalysisAgent._extract_responses_text({"output_text": "dict-direct"}),
            "dict-direct",
        )

    def test_empty_chat_response_falls_back_to_responses_api(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: None))
        responses_result = SimpleNamespace(
            output_text="{\"answer\": \"ok\"}",
            usage={"input_tokens": 12, "output_tokens": 4},
        )
        with patch.object(settings, "RETRY_MAX_ATTEMPTS", 1), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch(
            "agents.analysis_agent.call_chat_completion",
            return_value=_chat_response(None),
        ) as chat_call, patch(
            "agents.analysis_agent.call_responses", return_value=responses_result
        ) as responses_call:
            self.assertEqual(agent._call_cheap_llm("prompt"), '{"answer": "ok"}')

        chat_call.assert_called_once()
        responses_call.assert_called_once()
        self.assertEqual(
            responses_call.call_args.kwargs["text"],
            {"format": {"type": "json_object"}},
        )

    def test_responses_fallback_retries_with_portable_arguments(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: None))
        response = SimpleNamespace(output_text='{"answer": "ok"}')
        with patch.object(settings, "RETRY_MAX_ATTEMPTS", 1), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch(
            "agents.analysis_agent.call_chat_completion",
            return_value=_chat_response(None),
        ), patch(
            "agents.analysis_agent.call_responses",
            side_effect=[TypeError("unsupported optional argument"), response],
        ) as responses_call:
            self.assertEqual(agent._call_cheap_llm("prompt"), '{"answer": "ok"}')

        self.assertEqual(responses_call.call_count, 2)
        self.assertIn("temperature", responses_call.call_args_list[0].kwargs)
        self.assertIn("text", responses_call.call_args_list[0].kwargs)
        self.assertEqual(
            responses_call.call_args_list[1].kwargs,
            {"model": settings.CHEAP_LLM.model_name, "input": "prompt"},
        )

    def test_empty_provider_responses_raise_and_remain_retryable(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.cheap_client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: None))
        with patch.object(settings, "RETRY_MAX_ATTEMPTS", 2), patch.object(
            settings, "RETRY_MIN_WAIT", 0
        ), patch.object(settings, "RETRY_MAX_WAIT", 0), patch.object(
            settings, "TOKEN_TRACKING_ENABLED", False
        ), patch(
            "agents.analysis_agent.call_chat_completion",
            return_value=_chat_response(None),
        ) as chat_call, patch(
            "agents.analysis_agent.call_responses",
            return_value=SimpleNamespace(output_text=""),
        ) as responses_call:
            with self.assertRaises(LLMResponseError):
                agent._call_cheap_llm("prompt")

        self.assertEqual(chat_call.call_count, 2)
        self.assertEqual(responses_call.call_count, 2)

    def test_token_usage_accepts_chat_and_responses_field_names(self):
        with patch.object(settings, "TOKEN_TRACKING_ENABLED", True), patch(
            "utils.token_counter.token_counter.add"
        ) as add:
            AnalysisAgent._record_token_usage(
                "model-a", 7, {"input_tokens": 12, "output_tokens": 4}
            )
            AnalysisAgent._record_token_usage(
                "model-b", 7, SimpleNamespace(prompt_tokens=9, completion_tokens=3)
            )

        self.assertEqual(add.call_args_list[0].args, ("model-a", 12, 4))
        self.assertEqual(add.call_args_list[1].args, ("model-b", 9, 3))

    def test_deep_analysis_rejects_metadata_only_but_keeps_custom_template_fields(self):
        template = {
            "modules": [
                {"id": "summary", "enabled": True},
                {"id": "full_text_tldr", "enabled": True},
            ]
        }
        with self.assertRaisesRegex(ValueError, "可渲染内容"):
            validate_deep_analysis_payload({"provider_error": "temporary"}, template)

        payload = {"full_text_tldr": "基于全文的可渲染总结"}
        self.assertEqual(validate_deep_analysis_payload(payload, template), payload)

    def test_deep_analysis_treats_metadata_only_provider_output_as_retryable_failure(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.deep_template = {
            "modules": [
                {
                    "id": "summary",
                    "enabled": True,
                    "format": "quote",
                    "prompt": "概括论文内容",
                }
            ],
            "prompts": {},
        }
        agent._download_and_parse_pdf = lambda _url: "paper text"
        agent._call_smart_llm = lambda _prompt: '{"provider_error": "empty output"}'

        self.assertIsNone(
            agent.deep_analyze(
                "A paper",
                "https://arxiv.org/pdf/2501.12345v1.pdf",
                "abstract",
            )
        )

    def test_deep_analysis_prompt_explicitly_describes_list_fields(self):
        agent = AnalysisAgent.__new__(AnalysisAgent)
        agent.deep_template = {
            "modules": [
                {
                    "id": "summary",
                    "enabled": True,
                    "format": "quote",
                    "prompt": "概括论文内容",
                },
                {
                    "id": "innovations",
                    "enabled": True,
                    "format": "list",
                    "prompt": "列出创新点",
                },
            ],
            "prompts": {"analysis_template": "{field_prompts}"},
        }
        agent._download_and_parse_pdf = lambda _url: "paper text"
        prompts = []

        def _response(prompt):
            prompts.append(prompt)
            return '{"summary": "内容", "innovations": ["创新"]}'

        agent._call_smart_llm = _response
        result = agent.deep_analyze(
            "A paper",
            "https://arxiv.org/pdf/2501.12345v1.pdf",
            "abstract",
        )

        self.assertEqual(result["innovations"], ["创新"])
        self.assertIn('"innovations": ["...", "..."]', prompts[0])


if __name__ == "__main__":
    unittest.main()
