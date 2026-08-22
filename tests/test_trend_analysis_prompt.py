"""趋势分析自定义提示词：配置往返 + 技能指令替换。"""

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.config_io import build_config_dict, flatten_config_dict  # noqa: E402


class TrendPromptConfigRoundTripTests(unittest.TestCase):
    def test_analysis_prompt_survives_build_and_flatten(self):
        config = build_config_dict(trend_analysis_prompt="聚焦实验进展")
        self.assertEqual(config["trend_research"]["analysis_prompt"], "聚焦实验进展")

        flat = flatten_config_dict(config)
        self.assertEqual(flat["trend_analysis_prompt"], "聚焦实验进展")

        # 缺省回落为空字符串
        empty = build_config_dict()
        self.assertEqual(empty["trend_research"]["analysis_prompt"], "")


class TrendAgentPromptOverrideTests(unittest.TestCase):
    def test_custom_prompt_replaces_comprehensive_skill_instruction(self):
        from agents.trend_agent import TrendAgent
        from config import settings

        agent = TrendAgent.__new__(TrendAgent)  # 跳过 LLM 客户端初始化
        agent.skills = {
            "skills": [
                {
                    "name": "comprehensive_analysis",
                    "label": "综合趋势分析",
                    "instruction": "内置指令",
                }
            ]
        }

        captured: list[dict] = []

        def fake_run(skill, *_args, **_kwargs):
            captured.append(skill)
            return "分析结果"

        with (
            patch.object(settings, "RESEARCH_ENABLED_SKILLS", ["comprehensive_analysis"]),
            patch.object(settings, "RESEARCH_ANALYSIS_PROMPT", " 自定义指令 "),
            patch.object(agent, "_run_single_skill", side_effect=fake_run),
        ):
            results = agent.analyze_trends(
                ["quantum"],
                [],
                date(2026, 1, 1),
                date(2026, 8, 1),
                {},
            )

        self.assertEqual(results, {"comprehensive_analysis": "分析结果"})
        self.assertEqual(captured[0]["instruction"], "自定义指令")
        self.assertEqual(captured[0]["label"], "综合趋势分析")
