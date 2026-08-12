import json
import logging
import hashlib
import math
import re
import threading
import unicodedata
import requests
import fitz  # pymupdf
from typing import Optional, Dict, Any, List
from pathlib import Path
from openai import OpenAI
from pydantic import BaseModel, ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

from config import settings
from parsers.mineru_parser import MineruParser
from utils.llm_request_pool import call_chat_completion

logger = logging.getLogger(__name__)


class ScoreValidationError(ValueError):
    """Raised when a screening-model response cannot safely be used as a score.

    A malformed score must fail the paper stage instead of being silently
    persisted.  The daily pipeline can then retry that exact version without
    reporting an arbitrary recommendation.
    """


def _normalized_person_name(value: Any) -> str:
    """Return a deterministic comparison key for a human name.

    This intentionally remains an *exact* name comparison after harmless
    presentation differences (case, whitespace, punctuation and Unicode
    width) are removed.  It is not fuzzy matching: similar-looking authors
    must not receive an expert bonus accidentally.
    """
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)


def _finite_number(value: Any, field_name: str) -> float:
    """Parse one JSON numeric value without accepting bool/NaN/infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScoreValidationError(f"{field_name} 必须是有限数字")
    number = float(value)
    if not math.isfinite(number):
        raise ScoreValidationError(f"{field_name} 必须是有限数字")
    return number


# ======================================================================
# Pydantic数据模型：用于验证和结构化LLM输出
# ======================================================================


class WeightedScoreResponse(BaseModel):
    """
    加权评分响应模型（新策略）。

    属性:
        total_score (float): 总分（关键词加权分 + 作者附加分）
        keyword_scores (Dict[str, float]): 每个关键词的相关度评分（0-10）
        author_bonus (float): 作者附加分
        expert_authors_found (List[str]): 发现的专家作者列表
        passing_score (float): 动态及格分
        is_qualified (bool): 是否及格
        reasoning (str): 评分理由和分析
        tldr (str): 一句话总结论文的研究问题和结果
        extracted_keywords (List[str]): 从标题和摘要中提取的关键词
    """

    total_score: float
    keyword_scores: Dict[str, float]
    author_bonus: float
    expert_authors_found: List[str]
    passing_score: float
    is_qualified: bool
    reasoning: str
    tldr: str
    extracted_keywords: List[str]


class Stage2Response(BaseModel):
    """
    深度分析响应模型（可配置字段）。

    属性根据 settings.ENABLED_ANALYSIS_FIELDS 动态使用。
    """

    chinese_title: Optional[str] = None
    summary: Optional[str] = None
    innovations: Optional[List[str]] = None
    methodology: Optional[str] = None
    key_results: Optional[str] = None
    tech_stack: Optional[List[str]] = None
    strengths: Optional[List[str]] = None
    limitations: Optional[List[str]] = None
    relevance_to_keywords: Optional[str] = None
    future_work: Optional[str] = None
    custom_answers: Optional[Dict[str, str]] = None


class AnalysisAgent:
    """
    论文分析Agent（新策略：加权评分系统）。

    职责:
    - 基于关键词权重对论文进行加权评分
    - 检测专家作者并给予附加分
    - 计算动态及格分并判断是否合格
    - 对及格论文进行深度分析（使用可配置模板）
    """

    def __init__(self):
        # 初始化两个不同性能LLM客户端
        self.cheap_client = OpenAI(
            api_key=settings.CHEAP_LLM.api_key, base_url=settings.CHEAP_LLM.base_url
        )
        self.smart_client = OpenAI(
            api_key=settings.SMART_LLM.api_key, base_url=settings.SMART_LLM.base_url
        )

        # 初始化 MinerU PDF 解析器
        self.mineru_parser = MineruParser()

        # 加载报告模板以获取prompt配置
        self.basic_template = settings.load_report_template("basic_report_template.json")
        self.deep_template = settings.load_report_template("deep_analysis_template.json")

    # ======================================================================
    # 带重试的 LLM / HTTP 调用封装
    # ======================================================================

    def _call_cheap_llm(self, prompt: str) -> str:
        """调用低成本LLM（JSON模式），带自动重试。"""
        estimated_prompt_tokens = len(prompt) // 4

        @retry(
            stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
            wait=wait_exponential(min=settings.RETRY_MIN_WAIT, max=settings.RETRY_MAX_WAIT),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _do_call():
            try:
                resp = call_chat_completion(
                    self.cheap_client,
                    model=settings.CHEAP_LLM.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=settings.CHEAP_LLM.temperature,
                    response_format={"type": "json_object"},
                )
            except Exception:
                if settings.TOKEN_TRACKING_ENABLED:
                    from utils.token_counter import token_counter

                    token_counter.add(settings.CHEAP_LLM.model_name, estimated_prompt_tokens, 0)
                raise
            if settings.TOKEN_TRACKING_ENABLED and resp.usage:
                from utils.token_counter import token_counter

                token_counter.add(
                    settings.CHEAP_LLM.model_name,
                    resp.usage.prompt_tokens,
                    resp.usage.completion_tokens,
                )
            return resp.choices[0].message.content

        return _do_call()

    def _call_cheap_llm_plain(self, prompt: str) -> str:
        """调用低成本LLM（纯文本模式），带自动重试。"""
        estimated_prompt_tokens = len(prompt) // 4

        @retry(
            stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
            wait=wait_exponential(min=settings.RETRY_MIN_WAIT, max=settings.RETRY_MAX_WAIT),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _do_call():
            try:
                resp = call_chat_completion(
                    self.cheap_client,
                    model=settings.CHEAP_LLM.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
            except Exception:
                if settings.TOKEN_TRACKING_ENABLED:
                    from utils.token_counter import token_counter

                    token_counter.add(settings.CHEAP_LLM.model_name, estimated_prompt_tokens, 0)
                raise
            if settings.TOKEN_TRACKING_ENABLED and resp.usage:
                from utils.token_counter import token_counter

                token_counter.add(
                    settings.CHEAP_LLM.model_name,
                    resp.usage.prompt_tokens,
                    resp.usage.completion_tokens,
                )
            return resp.choices[0].message.content.strip()

        return _do_call()

    def _call_smart_llm(self, prompt: str) -> str:
        """调用高性能LLM（JSON模式），带自动重试。"""
        estimated_prompt_tokens = len(prompt) // 4

        @retry(
            stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
            wait=wait_exponential(min=settings.RETRY_MIN_WAIT, max=settings.RETRY_MAX_WAIT),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _do_call():
            try:
                resp = call_chat_completion(
                    self.smart_client,
                    model=settings.SMART_LLM.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=settings.SMART_LLM.temperature,
                    response_format={"type": "json_object"},
                )
            except Exception:
                if settings.TOKEN_TRACKING_ENABLED:
                    from utils.token_counter import token_counter

                    token_counter.add(settings.SMART_LLM.model_name, estimated_prompt_tokens, 0)
                raise
            if settings.TOKEN_TRACKING_ENABLED and resp.usage:
                from utils.token_counter import token_counter

                token_counter.add(
                    settings.SMART_LLM.model_name,
                    resp.usage.prompt_tokens,
                    resp.usage.completion_tokens,
                )
            return resp.choices[0].message.content

        return _do_call()

    def _download_pdf_bytes(self, pdf_url: str) -> bytes:
        """下载PDF内容，带自动重试。"""

        @retry(
            stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),
            wait=wait_exponential(min=settings.RETRY_MIN_WAIT, max=settings.RETRY_MAX_WAIT),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _do_download():
            headers = {
                "User-Agent": "ArxivDailyResearcher/2.0 (https://github.com/yzr278892/arxiv-daily-researcher; yzr278892@gmail.com)"
            }
            resp = requests.get(pdf_url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.content

        return _do_download()

    def _clean_json_string(self, json_str: str) -> str:
        """清理LLM响应中的Markdown代码块标记和非法转义字符。"""
        # 移除Markdown代码块标记
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        json_str = json_str.strip()

        # 修复常见的非法转义字符（LaTeX符号等）
        # 使用原始字符串处理，避免Python本身的转义问题
        import re

        # 找到所有字符串值（在双引号内的内容）
        def fix_escapes_in_match(match):
            content = match.group(1)
            # 只保留合法的JSON转义序列：\" \\ \/ \b \f \n \r \t \uXXXX
            # 将其他反斜杠转义为双反斜杠
            result = ""
            i = 0
            while i < len(content):
                if content[i] == "\\":
                    if i + 1 < len(content):
                        next_char = content[i + 1]
                        # 合法的转义字符
                        if next_char in ['"', "\\", "/", "b", "f", "n", "r", "t"]:
                            result += content[i : i + 2]
                            i += 2
                        # Unicode转义
                        elif next_char == "u" and i + 5 < len(content):
                            result += content[i : i + 6]
                            i += 6
                        # 非法转义，转义反斜杠本身
                        else:
                            result += "\\\\"
                            i += 1
                    else:
                        result += "\\\\"
                        i += 1
                else:
                    result += content[i]
                    i += 1
            return f'"{result}"'

        # 匹配JSON字符串值（简化版，不处理嵌套）
        json_str = re.sub(r'"((?:[^"\\]|\\.)*)"', fix_escapes_in_match, json_str)

        return json_str

    # ======================================================================
    # 新策略：加权评分系统
    # ======================================================================

    def score_paper_with_keywords(
        self, title: str, authors: str | List[str], abstract: str, keywords_dict: Dict[str, float]
    ) -> WeightedScoreResponse:
        """
        使用加权关键词系统对论文进行评分。

        评分公式:
        总分 = Σ(关键词相关度 × 关键词权重) + 作者附加分

        参数:
            title (str): 论文标题
            authors (str | List[str]): 作者列表。调用方应尽量传入原始作者列表，
                以便确定性校验专家作者加分。
            abstract (str): 论文摘要
            keywords_dict (Dict[str, float]): 关键词-权重字典

        返回:
            WeightedScoreResponse: 包含详细评分信息的响应对象
        """
        # 评分输入也是配置的一部分。先校验它，避免异常配置把 NaN 或
        # 负权重一路传到报告和排序逻辑中。
        max_score = _finite_number(
            settings.MAX_SCORE_PER_KEYWORD, "MAX_SCORE_PER_KEYWORD"
        )
        if max_score <= 0:
            raise ScoreValidationError("MAX_SCORE_PER_KEYWORD 必须大于 0")

        normalized_keywords: Dict[str, float] = {}
        for keyword, weight in keywords_dict.items():
            if not isinstance(keyword, str) or not keyword.strip():
                raise ScoreValidationError("评分关键词必须是非空字符串")
            numeric_weight = _finite_number(weight, f"关键词 {keyword!r} 的权重")
            if numeric_weight < 0:
                raise ScoreValidationError(f"关键词 {keyword!r} 的权重不能为负数")
            normalized_keywords[keyword] = numeric_weight
        if not normalized_keywords:
            raise ScoreValidationError("至少需要一个评分关键词")

        if isinstance(authors, str):
            author_names = [name.strip() for name in authors.split(",") if name.strip()]
        elif isinstance(authors, list) and all(isinstance(name, str) for name in authors):
            author_names = [name.strip() for name in authors if name.strip()]
        else:
            raise ScoreValidationError("论文作者必须是字符串或字符串列表")
        authors_text = ", ".join(author_names)

        # 计算总权重和及格分
        total_weight = math.fsum(normalized_keywords.values())
        passing_score = _finite_number(
            settings.calculate_passing_score(total_weight), "动态及格分"
        )
        if passing_score < 0:
            raise ScoreValidationError("动态及格分不能为负数")

        author_bonus_points = 0.0
        if settings.ENABLE_AUTHOR_BONUS:
            author_bonus_points = _finite_number(
                settings.AUTHOR_BONUS_POINTS, "AUTHOR_BONUS_POINTS"
            )
            if author_bonus_points < 0:
                raise ScoreValidationError("AUTHOR_BONUS_POINTS 不能为负数")

        # 构建关键词列表字符串
        keywords_list = "\n".join(
            [f"  - {kw} (权重: {weight:.1f})" for kw, weight in normalized_keywords.items()]
        )

        prompt = f"""你是一名学术论文评审专家。请基于以下关键词对论文进行相关性评分，并提取论文信息。

研究背景:
{settings.RESEARCH_CONTEXT if settings.RESEARCH_CONTEXT else "通用学术研究"}

评分关键词及权重:
{keywords_list}

论文信息:
标题: {title}
作者: {authors_text}
摘要: {abstract}

评分任务:
1. 理解论文的研究内容和主题
2. 对每个关键词评估相关度（0-{max_score:g}分）:
   - 0分: 完全无关
   - {max_score / 2:g}分: 有一定关联
   - {max_score:g}分: 高度相关，核心内容
3. 计算加权总分: Σ(关键词相关度 × 关键词权重)
4. 用一句话总结论文研究的问题和结果（TLDR）
5. 从标题和摘要中提取5-8个核心关键词（英文）

作者加分由系统根据原始作者列表做确定性精确校验；不要猜测专家作者，
也不要输出作者加分或 expert_authors_found 字段。

评分标准:
- 关键词总权重: {total_weight:.1f}
- 动态及格分: {passing_score:.1f}
- 每个关键词最高相关度: {max_score:g} 分

输出格式: JSON对象，包含以下字段:
{{
  "keyword_scores": {{"关键词1": 8.0, "关键词2": 5.0, ...}},
  "reasoning": "详细的评分理由和分析",
  "tldr": "一句话总结论文研究的核心问题和主要结果",
  "extracted_keywords": ["keyword1", "keyword2", "keyword3", ...]
}}

要求:
- keyword_scores 必须包含所有给定的关键词
- keyword_scores 必须且只能包含给定的所有关键词，键名必须逐字一致
- 每个关键词的评分范围: 0-{max_score:g}
- reasoning 应简明扼要地说明论文与关键词的相关性
- tldr 应该是一句完整的话，包含研究问题和主要结果
- extracted_keywords 应提取5-8个最能代表论文内容的关键词或短语
"""

        try:
            content = self._call_cheap_llm(prompt)
            content = self._clean_json_string(content)

            try:
                data = json.loads(content)
            except json.JSONDecodeError as json_err:
                logger.error(f"JSON解析失败: {json_err}")
                logger.error(f"原始内容（前500字符）: {content[:500]}")
                raise

            if not isinstance(data, dict):
                raise ScoreValidationError("评分模型返回的 JSON 顶层必须是对象")

            # Do not substitute defaults here.  A missing TLDR/reasoning used
            # to become a seemingly successful, permanently cached score and
            # was the primary source of lost report content after a restart.
            raw_keyword_scores = data.get("keyword_scores")
            if not isinstance(raw_keyword_scores, dict):
                raise ScoreValidationError("keyword_scores 必须是对象")

            expected_keywords = set(normalized_keywords)
            returned_keywords = set(raw_keyword_scores)
            missing_keywords = expected_keywords.difference(returned_keywords)
            unexpected_keywords = returned_keywords.difference(expected_keywords)
            if missing_keywords or unexpected_keywords:
                details = []
                if missing_keywords:
                    details.append("缺少关键词: " + ", ".join(sorted(missing_keywords)))
                if unexpected_keywords:
                    details.append("包含未配置关键词: " + ", ".join(sorted(unexpected_keywords)))
                raise ScoreValidationError("keyword_scores 键集合无效（" + "；".join(details) + "）")

            keyword_scores: Dict[str, float] = {}
            for keyword in normalized_keywords:
                score = _finite_number(
                    raw_keyword_scores[keyword], f"关键词 {keyword!r} 的相关度"
                )
                if score < 0 or score > max_score:
                    raise ScoreValidationError(
                        f"关键词 {keyword!r} 的相关度必须在 0-{max_score:g} 之间"
                    )
                keyword_scores[keyword] = score

            reasoning = data.get("reasoning")
            if not isinstance(reasoning, str) or not reasoning.strip():
                raise ScoreValidationError("reasoning 必须是非空字符串")
            reasoning = reasoning.strip()

            tldr = data.get("tldr")
            if not isinstance(tldr, str) or not tldr.strip():
                raise ScoreValidationError("tldr 必须是非空字符串")
            tldr = tldr.strip()

            extracted_keywords = data.get("extracted_keywords", [])
            if not isinstance(extracted_keywords, list) or not all(
                isinstance(keyword, str) and keyword.strip() for keyword in extracted_keywords
            ):
                raise ScoreValidationError("extracted_keywords 必须是非空字符串列表")
            extracted_keywords = [keyword.strip() for keyword in extracted_keywords]

            # Expert-author evidence generated by an LLM is intentionally not
            # authoritative.  Restrict bonuses to the exact intersection of
            # configured names and the source's original author list.  This
            # eliminates both hallucinated and duplicate authors from score
            # calculation while still tolerating an older model that emits the
            # now-ignored field.
            configured_experts = {
                _normalized_person_name(name)
                for name in settings.EXPERT_AUTHORS
                if _normalized_person_name(name)
            }
            verified_experts: List[str] = []
            seen_expert_names = set()
            if settings.ENABLE_AUTHOR_BONUS:
                for author_name in author_names:
                    normalized_name = _normalized_person_name(author_name)
                    if (
                        normalized_name
                        and normalized_name in configured_experts
                        and normalized_name not in seen_expert_names
                    ):
                        verified_experts.append(author_name)
                        seen_expert_names.add(normalized_name)

            claimed_experts = data.get("expert_authors_found")
            if claimed_experts is not None:
                if not isinstance(claimed_experts, list) or not all(
                    isinstance(name, str) for name in claimed_experts
                ):
                    logger.warning("忽略评分模型返回的无效 expert_authors_found 字段")
                else:
                    claimed_normalized = {
                        _normalized_person_name(name)
                        for name in claimed_experts
                        if _normalized_person_name(name)
                    }
                    verified_normalized = {
                        _normalized_person_name(name) for name in verified_experts
                    }
                    if claimed_normalized != verified_normalized:
                        logger.warning(
                            "评分模型的专家作者声明与确定性校验不一致，已忽略模型声明"
                        )

            # 计算加权总分
            weighted_score = math.fsum(
                keyword_scores[kw] * weight for kw, weight in normalized_keywords.items()
            )

            # 计算作者附加分
            author_bonus = 0.0
            if settings.ENABLE_AUTHOR_BONUS and verified_experts:
                author_bonus = len(verified_experts) * author_bonus_points

            # 计算总分
            total_score = weighted_score + author_bonus

            # 判断是否及格
            is_qualified = total_score >= passing_score

            logger.info(
                f"论文评分完成 [{title[:50]}]: 总分={total_score:.1f}, 及格分={passing_score:.1f}, {'✅及格' if is_qualified else '❌未及格'}"
            )

            return WeightedScoreResponse(
                total_score=total_score,
                keyword_scores=keyword_scores,
                author_bonus=author_bonus,
                expert_authors_found=verified_experts,
                passing_score=passing_score,
                is_qualified=is_qualified,
                reasoning=reasoning,
                tldr=tldr,
                extracted_keywords=extracted_keywords,
            )

        except Exception as e:
            logger.error(f"论文评分失败 [{title[:50]}]: {e}")
            # A synthetic zero score hides outages and permanently loses TLDR
            # data. Let the pipeline persist the failure and retry the stage.
            raise RuntimeError(f"论文评分失败 [{title[:50]}]: {e}") from e

    # ======================================================================
    # 摘要翻译
    # ======================================================================

    def translate_abstract(self, abstract: str) -> str:
        """
        将英文摘要翻译为中文。

        参数:
            abstract (str): 英文摘要

        返回:
            str: 中文翻译，失败时返回空字符串
        """
        prompt = f"""请将以下学术论文摘要翻译为中文。要求：
1. 保持学术术语的准确性
2. 语句通顺流畅
3. 保留专业名词的英文（可在首次出现时标注）

英文摘要：
{abstract}

请直接输出中文翻译，不要添加任何说明或标记。"""

        try:
            translation = self._call_cheap_llm_plain(prompt)
            if not translation or not translation.strip():
                raise RuntimeError("LLM 返回空摘要翻译")
            logger.info(f"摘要翻译完成 [{abstract[:30]}...]")
            return translation.strip()

        except Exception as e:
            logger.error(f"摘要翻译失败: {e}")
            raise RuntimeError(f"摘要翻译失败: {e}") from e

    # ======================================================================
    # 深度分析（使用新模板系统）
    # ======================================================================

    def deep_analyze(
        self, title: str, pdf_url: str, abstract: str, fallback_to_abstract: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        对论文进行深度分析（使用新的模板系统）。

        参数:
            title (str): 论文标题
            pdf_url (str): PDF下载URL
            abstract (str): 论文摘要（作为降级方案）
            fallback_to_abstract (bool): PDF下载失败时是否使用摘要

        返回:
            Optional[Dict]: 分析结果字典，失败时返回None
        """
        # 尝试下载并解析PDF
        pdf_text = self._download_and_parse_pdf(pdf_url)

        if not pdf_text:
            if fallback_to_abstract:
                logger.warning(f"PDF解析失败 [{title[:50]}]，使用摘要作为降级方案")
                pdf_text = abstract
            else:
                logger.error(f"PDF解析失败 [{title[:50]}]，且未启用降级方案")
                return None

        # 从新模板获取配置
        modules = self.deep_template.get("modules", [])
        prompts_config = self.deep_template.get("prompts", {})

        # 获取启用的模块
        enabled_modules = [m for m in modules if m.get("enabled", True)]

        # 构建字段提示词字符串
        field_prompts_lines = []
        output_fields = []

        for module in enabled_modules:
            module_id = module.get("id")
            module_prompt = module.get("prompt", "")

            if module_id == "custom_questions":
                # 处理自定义问题
                questions = module.get("questions", [])
                if questions:
                    field_prompts_lines.append(f"\n自定义问题:")
                    for i, q in enumerate(questions, 1):
                        field_prompts_lines.append(f"{i}. {q}")
                    output_fields.append(
                        f'  "custom_answers": {{"问题1": "回答1", "问题2": "回答2", ...}}'
                    )
            else:
                # 普通模块
                field_prompts_lines.append(f"\n{module_id}: {module_prompt}")
                output_fields.append(f'  "{module_id}": "..."')

        fields_str = ",\n".join(output_fields)
        field_prompts_str = "\n".join(field_prompts_lines)

        # 使用模板中的系统提示词和用户提示词模板
        system_prompt = prompts_config.get("analysis_system", "你是一名学术论文分析专家。")
        analysis_template = prompts_config.get("analysis_template", "")

        # 构建最终prompt
        if analysis_template:
            # 使用模板中的格式
            prompt = analysis_template.format(
                title=title,
                content=pdf_text[:15000],
                research_context=(
                    settings.RESEARCH_CONTEXT if settings.RESEARCH_CONTEXT else "通用学术研究"
                ),
                field_prompts=field_prompts_str,
            )
        else:
            # 备用格式
            prompt = f"""论文标题: {title}

论文内容:
{pdf_text[:15000]}

研究背景:
{settings.RESEARCH_CONTEXT if settings.RESEARCH_CONTEXT else "通用学术研究"}

分析要求:
{field_prompts_str}

输出格式（JSON）:
{{
{fields_str}
}}
"""

        # 添加输出格式说明
        prompt += f"\n\n{prompts_config.get('field_output_format', '使用JSON格式输出。')}"

        try:
            content = self._call_smart_llm(prompt)
            content = self._clean_json_string(content)

            try:
                result = json.loads(content)
            except json.JSONDecodeError as json_err:
                logger.error(f"JSON解析失败: {json_err}")
                logger.error(f"原始内容（前500字符）: {content[:500]}")
                raise

            logger.info(f"深度分析完成 [{title[:50]}]")
            return result

        except Exception as e:
            logger.error(f"深度分析失败 [{title[:50]}]: {e}")
            import traceback

            traceback.print_exc()
            return None

    def _download_and_parse_pdf(self, pdf_url: str) -> Optional[str]:
        """
        下载PDF并提取文本内容。

        根据配置选择解析方式:
        - mineru: 优先使用 MinerU 云端 API 解析，失败时自动降级到 PyMuPDF
        - pymupdf: 直接使用 PyMuPDF 本地解析

        参数:
            pdf_url (str): PDF下载URL

        返回:
            Optional[str]: 提取的文本内容，失败时返回None
        """
        # 根据配置决定解析方式
        if settings.PDF_PARSER_MODE == "mineru":
            # 尝试 MinerU 云端解析
            text = self._parse_pdf_with_mineru(pdf_url)
            if text:
                return text
            # MinerU 失败，降级到 PyMuPDF
            logger.info("降级使用 PyMuPDF 本地解析")

        return self._parse_pdf_with_pymupdf(pdf_url)

    def _parse_pdf_with_mineru(self, pdf_url: str) -> Optional[str]:
        """
        使用 MinerU API 解析 PDF。

        参数:
            pdf_url (str): PDF下载URL

        返回:
            Optional[str]: 提取的文本内容，失败时返回None
        """
        if not self.mineru_parser.is_available():
            if not self.mineru_parser.is_configured():
                logger.warning("MinerU API 未配置（MINERU_API_KEY 为空），使用 PyMuPDF 本地解析")
            return None

        text = self.mineru_parser.parse_pdf(pdf_url)
        if text:
            logger.info(f"MinerU 解析成功，获取 {len(text)} 字符")
        return text

    def _parse_pdf_with_pymupdf(self, pdf_url: str) -> Optional[str]:
        """
        使用 PyMuPDF 本地解析 PDF。

        参数:
            pdf_url (str): PDF下载URL

        返回:
            Optional[str]: 提取的文本内容，失败时返回None
        """
        try:
            # 下载PDF（带自动重试）
            pdf_bytes = self._download_pdf_bytes(pdf_url)

            # 保存到临时文件
            temp_pdf = (
                settings.DOWNLOAD_DIR
                / f"temp_{hashlib.md5(pdf_url.encode()).hexdigest()[:16]}_{threading.get_ident()}.pdf"
            )
            with open(temp_pdf, "wb") as f:
                f.write(pdf_bytes)

            # 解析PDF（前20页），使用 try/finally 确保资源释放和临时文件清理
            try:
                with fitz.open(temp_pdf) as doc:
                    text = ""
                    for i, page in enumerate(doc):
                        if i >= 20:  # 只读前20页
                            break
                        text += page.get_text()
            finally:
                # 无论解析成功与否均清理临时文件
                if temp_pdf.exists():
                    temp_pdf.unlink()

            logger.info(f"PyMuPDF 解析成功，提取 {len(text)} 字符")
            return text

        except Exception as e:
            logger.error(f"PyMuPDF PDF下载/解析失败: {e}")
            return None
