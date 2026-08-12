"""
研究报告生成Agent（模块化版本）

支持通过JSON模板配置报告的结构和格式：
- 基本报告：每篇论文的元数据、摘要、TLDR、评分等
- 深度分析报告：及格论文的详细分析

模块化设计：
- 每个信息块作为独立模块
- 可配置模块的启用/禁用、顺序、格式、折叠等
- 支持自定义提示词
"""

import html
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import settings
from .modules.base_module import FormatHelper
from .modules.renderers import ModuleRendererFactory

logger = logging.getLogger(__name__)


class ReportGenerationError(RuntimeError):
    """Raised when an enabled report format cannot be rendered or persisted."""

# 数据源显示名称映射
SOURCE_DISPLAY_NAMES = {
    "arxiv": "ArXiv",
    "prl": "Physical Review Letters",
    "pra": "Physical Review A",
    "prb": "Physical Review B",
    "prc": "Physical Review C",
    "prd": "Physical Review D",
    "pre": "Physical Review E",
    "prx": "Physical Review X",
    "prxq": "PRX Quantum",
    "rmp": "Reviews of Modern Physics",
    "nature": "Nature",
    "nature_physics": "Nature Physics",
    "nature_communications": "Nature Communications",
    "science": "Science",
    "science_advances": "Science Advances",
    "npj_quantum_information": "npj Quantum Information",
    "quantum": "Quantum",
    "new_journal_of_physics": "New Journal of Physics",
}


class Reporter:
    """
    研究报告生成Agent（模块化版本）。

    职责:
    - 加载报告模板配置
    - 按数据源分别生成报告
    - 使用模块化渲染器生成各部分内容
    - 支持自定义格式和布局
    """

    def __init__(self):
        self.report_base_dir = settings.REPORTS_DIR / "daily_research"

        # 加载模板
        self.basic_template = settings.load_report_template("basic_report_template.json")
        self.deep_template = settings.load_report_template("deep_analysis_template.json")

        # 初始化格式化工具和渲染器工厂
        admonition_style = self.basic_template.get("global", {}).get("admonition_style", "mkdocs")
        self.format_helper = FormatHelper(admonition_style)
        self.renderer_factory = ModuleRendererFactory(self.format_helper, self.deep_template)

    def get_source_display_name(self, source: str) -> str:
        """获取数据源的显示名称"""
        return SOURCE_DISPLAY_NAMES.get(source, source.upper())

    @staticmethod
    def _write_text_atomic(filepath: Path, content: str) -> Path:
        """Durably write a complete report before exposing its final path."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=filepath.parent,
                prefix=f".{filepath.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, filepath)
            return filepath
        except Exception:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("无法清理报告临时文件: %s", temporary_path)
            raise

    @staticmethod
    def _paper_status_label(paper: Dict[str, Any]) -> str:
        """Build a visible version/retry label for a paper entry."""
        paper_meta = paper.get("paper_metadata")
        revision = paper.get("revision") or {}
        version = getattr(paper_meta, "version", None)
        if revision:
            current = revision.get("version", version)
            previous = revision.get("previous_version")
            pushed_at = revision.get("previous_pushed_at")
            pushed_text = str(pushed_at or "").replace("T", " ")[:19]
            label = f"🔁 修订版 v{current}" if current is not None else "🔁 修订版"
            if previous is not None:
                label += f"（上一版本 v{previous}"
                if pushed_text:
                    label += f" 于 {pushed_text} 推送"
                label += "）"
            return label
        if paper.get("is_retry"):
            return "↻ 重试"
        if version is not None:
            return f"v{version}"
        return ""

    def generate_reports_by_source(
        self,
        scored_papers_by_source: Dict[str, List[Dict[str, Any]]],
        keywords_dict: Dict[str, float],
        analyses_by_source: Dict[str, List[Dict[str, Any]]] = None,
        token_usage: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Path]:
        """
        按数据源生成分开的报告。

        参数:
            scored_papers_by_source: {数据源: 论文列表}
            keywords_dict: 关键词-权重字典
            analyses_by_source: {数据源: 深度分析列表}（可选）

        返回:
            Dict[str, Path]: {数据源: 报告文件路径}
        """
        if analyses_by_source is None:
            analyses_by_source = {}

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        report_paths = {}

        for source, papers in scored_papers_by_source.items():
            if not papers:
                continue

            display_name = self.get_source_display_name(source)
            analyses = analyses_by_source.get(source, [])
            has_deep_analysis = len(analyses) > 0
            all_paper_count = len(papers)
            qualified_count = sum(
                1 for paper in papers if paper["score_response"].is_qualified
            )
            report_papers = (
                papers
                if settings.INCLUDE_ALL_IN_REPORT
                else [paper for paper in papers if paper["score_response"].is_qualified]
            )
            qualified_only = not settings.INCLUDE_ALL_IN_REPORT

            # Markdown 报告（如果启用）
            if settings.ENABLE_MARKDOWN_REPORT:
                if settings.REPORTS_BY_SOURCE:
                    md_dir = self.report_base_dir / "markdown" / source
                else:
                    md_dir = self.report_base_dir / "markdown"
                filepath = md_dir / f"{source.upper()}_Report_{timestamp}.md"
                try:
                    report_paths[source] = self._generate_single_source_report(
                        filepath=filepath,
                        source=source,
                        display_name=display_name,
                        papers=report_papers,
                        all_paper_count=all_paper_count,
                        qualified_count=qualified_count,
                        qualified_only=qualified_only,
                        keywords_dict=keywords_dict,
                        analyses=analyses,
                        has_deep_analysis=has_deep_analysis,
                        token_usage=token_usage,
                    )
                except ReportGenerationError:
                    raise
                except Exception as exc:
                    raise ReportGenerationError(
                        f"[{source}] Markdown 报告生成失败 ({filepath}): {exc}"
                    ) from exc
                logger.info(f"[{source}] Markdown 报告已生成: {filepath}")

            # 生成 HTML 报告（如果启用）
            if settings.ENABLE_HTML_REPORT:
                # HTML 报告目录: reports/html/[source]/
                if settings.REPORTS_BY_SOURCE:
                    html_dir = self.report_base_dir / "html" / source
                else:
                    html_dir = self.report_base_dir / "html"
                html_filepath = html_dir / f"{source.upper()}_Report_{timestamp}.html"
                try:
                    report_paths[f"{source}_html"] = self._generate_html_report(
                        filepath=html_filepath,
                        source=source,
                        display_name=display_name,
                        papers=report_papers,
                        all_paper_count=all_paper_count,
                        qualified_count=qualified_count,
                        qualified_only=qualified_only,
                        keywords_dict=keywords_dict,
                        analyses=analyses,
                        has_deep_analysis=has_deep_analysis,
                        token_usage=token_usage,
                    )
                except ReportGenerationError:
                    raise
                except Exception as exc:
                    raise ReportGenerationError(
                        f"[{source}] HTML 报告生成失败 ({html_filepath}): {exc}"
                    ) from exc

        return report_paths

    def _generate_single_source_report(
        self,
        filepath: Path,
        source: str,
        display_name: str,
        papers: List[Dict[str, Any]],
        keywords_dict: Dict[str, float],
        analyses: List[Dict[str, Any]],
        has_deep_analysis: bool,
        all_paper_count: Optional[int] = None,
        qualified_count: Optional[int] = None,
        qualified_only: bool = False,
        token_usage: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """生成单个数据源的报告"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today = datetime.now().strftime("%Y-%m-%d")

        # 计算统计信息
        total_papers = all_paper_count if all_paper_count is not None else len(papers)
        if qualified_count is None:
            qualified_count = sum(1 for p in papers if p["score_response"].is_qualified)
        displayed_count = len(papers)
        analyzed_count = len(analyses)

        total_weight = sum(keywords_dict.values())
        passing_score = settings.calculate_passing_score(total_weight)

        # 按总分排序
        sorted_papers = sorted(papers, key=lambda x: x["score_response"].total_score, reverse=True)

        # 获取布局配置
        layout = self.basic_template.get("layout", {})

        # 开始生成报告
        lines = []

        # 报告标题
        title_template = layout.get("report_title_template", "📊 {source_name} 研究报告 ({date})")
        report_title = title_template.format(source_name=display_name, date=today)
        lines.append(f"# {report_title}")
        lines.append("")
        lines.append(f"> 生成时间: {timestamp}")
        lines.append(f"> 数据源: {display_name}")
        if qualified_only:
            lines.append(
                f"> 显示范围: 仅及格论文（{displayed_count}/{total_papers} 篇）；"
                "其余论文已完成评分并归档，不会在后续日报重复出现。"
            )
        lines.append("")

        # 数据源说明
        if source != "arxiv":
            lines.append("> ⚠️ **注意**: 该数据源不支持PDF下载，仅提供评分和摘要翻译，无深度分析")
            lines.append("")

        # ========== 配置信息 ==========
        if layout.get("show_config_section", True):
            lines.extend(self._generate_config_section(keywords_dict, passing_score))

        # ========== 统计汇总 ==========
        if layout.get("show_stats_section", True):
            lines.extend(
                self._generate_stats_section(
                    total_papers,
                    qualified_count,
                    analyzed_count,
                    has_deep_analysis,
                    displayed_count=displayed_count,
                    qualified_only=qualified_only,
                )
            )

        # ========== 及格论文详细信息 ==========
        show_qualified = layout.get("show_qualified_section", True) and qualified_count > 0
        if show_qualified:
            section_title = layout.get("qualified_section_title", "⭐ 及格论文详细分析")
            lines.append(f"## {section_title}")
            lines.append("")

            qualified_papers = [p for p in sorted_papers if p["score_response"].is_qualified]

            for idx, paper in enumerate(qualified_papers, 1):
                paper_lines = self._render_paper_section(
                    paper, keywords_dict, analyses, idx, is_qualified_section=True
                )
                lines.extend(paper_lines)

        if not sorted_papers and qualified_only:
            lines.append("## 📋 推荐结果")
            lines.append("")
            lines.append("本次没有达到通过分数的论文；全部候选论文均已完成评分与归档。")
            lines.append("")

        # ========== 所有论文详细信息 ==========
        remaining_papers = (
            [p for p in sorted_papers if not p["score_response"].is_qualified]
            if show_qualified
            else sorted_papers
        )
        if layout.get("show_all_papers_section", True) and remaining_papers:
            if show_qualified:
                section_title = layout.get("remaining_papers_section_title", "📋 其他论文列表")
            else:
                section_title = layout.get("all_papers_section_title", "📋 所有论文列表")
            lines.append(f"## {section_title}")
            lines.append("")

            qualified_icon = layout.get("qualified_icon", "✅")
            unqualified_icon = layout.get("unqualified_icon", "❌")

            for idx, paper in enumerate(remaining_papers, 1):
                paper_lines = self._render_paper_section(
                    paper,
                    keywords_dict,
                    [],
                    idx,
                    is_qualified_section=False,
                    qualified_icon=qualified_icon,
                    unqualified_icon=unqualified_icon,
                )
                lines.extend(paper_lines)

        # ========== Token 消耗统计 ==========
        if settings.TOKEN_TRACKING_ENABLED and token_usage and token_usage.get("has_data"):
            total = token_usage.get("total", 0)
            tp = token_usage.get("total_prompt", 0)
            tc = token_usage.get("total_completion", 0)
            by_model = token_usage.get("by_model", {})
            lines.append("## Token 消耗统计")
            lines.append("")
            lines.append(f"- **总计**: {total:,} tokens（输入 {tp:,} / 输出 {tc:,}）")
            if len(by_model) > 1:
                lines.append("")
                lines.append("| 模型 | 输入 | 输出 | 合计 |")
                lines.append("|------|------|------|------|")
                for model, usage in by_model.items():
                    lines.append(
                        f"| {model} | {usage['prompt']:,} | {usage['completion']:,} | {usage['total']:,} |"
                    )
            lines.append("")

        try:
            self._write_text_atomic(filepath, "\n".join(lines))
            logger.info(f"  - 总论文数: {total_papers}")
            logger.info(f"  - 及格论文: {qualified_count}")
            if has_deep_analysis:
                logger.info(f"  - 深度分析: {analyzed_count}")
        except Exception as e:
            logger.error(f"报告生成失败: {e}")
            raise ReportGenerationError(f"Markdown 报告写入失败 ({filepath}): {e}") from e
        return filepath

    def _generate_config_section(
        self, keywords_dict: Dict[str, float], passing_score: float
    ) -> List[str]:
        """生成配置信息部分"""
        lines = []
        total_weight = sum(keywords_dict.values())

        lines.append("## 📌 配置信息")
        lines.append("")

        # 关键词列表
        lines.append(f"### 关键词列表（共 {len(keywords_dict)} 个，总权重 {total_weight:.1f}）")
        lines.append("")
        lines.append("| 关键词 | 权重 | 类型 |")
        lines.append("|--------|------|------|")
        for kw, weight in sorted(keywords_dict.items(), key=lambda x: x[1], reverse=True):
            kw_type = "主要" if weight >= 1.0 else "次要"
            lines.append(f"| {kw} | {weight:.1f} | {kw_type} |")
        lines.append("")

        # 评分设置
        lines.append("### 评分设置")
        lines.append("")
        lines.append(f"- **每个关键词最大分**: {settings.MAX_SCORE_PER_KEYWORD}")
        lines.append(
            f"- **及格分公式**: {settings.PASSING_SCORE_BASE} + {settings.PASSING_SCORE_WEIGHT_COEFFICIENT} × 总权重"
        )
        lines.append(f"- **当前及格分**: {passing_score:.1f}")
        if settings.ENABLE_AUTHOR_BONUS:
            lines.append(f"- **作者加分**: 启用（{settings.AUTHOR_BONUS_POINTS}分/专家）")
            if settings.EXPERT_AUTHORS:
                lines.append(f"- **专家作者**: {', '.join(settings.EXPERT_AUTHORS)}")
        lines.append("")

        return lines

    def _generate_stats_section(
        self,
        total_papers: int,
        qualified_count: int,
        analyzed_count: int,
        has_deep_analysis: bool,
        displayed_count: Optional[int] = None,
        qualified_only: bool = False,
    ) -> List[str]:
        """生成统计汇总部分"""
        lines = []
        lines.append("## 📈 论文统计")
        lines.append("")
        lines.append(f"- **总抓取**: {total_papers} 篇")
        if total_papers > 0:
            lines.append(
                f"- **及格论文**: {qualified_count} 篇 ({qualified_count / total_papers * 100:.1f}%)"
            )
        else:
            lines.append(f"- **及格论文**: {qualified_count} 篇")
        if has_deep_analysis:
            lines.append(f"- **深度分析**: {analyzed_count} 篇")
        if qualified_only:
            lines.append(f"- **报告展示**: {displayed_count or 0} 篇（仅及格论文）")
        lines.append("")
        lines.append("---")
        lines.append("")
        return lines

    def _render_paper_section(
        self,
        paper: Dict[str, Any],
        keywords_dict: Dict[str, float],
        analyses: List[Dict[str, Any]],
        idx: int,
        is_qualified_section: bool = False,
        qualified_icon: str = "✅",
        unqualified_icon: str = "❌",
    ) -> List[str]:
        """
        使用模块化渲染器渲染单篇论文。

        参数:
            paper: 论文数据
            keywords_dict: 关键词字典
            analyses: 深度分析列表
            idx: 序号
            is_qualified_section: 是否在及格论文部分
            qualified_icon: 及格图标
            unqualified_icon: 未及格图标

        返回:
            List[str]: 渲染后的行列表
        """
        lines = []
        score_resp = paper["score_response"]
        paper_meta = paper.get("paper_metadata")

        # 准备数据（添加keywords_dict供scoring模块使用）
        paper_data = {**paper, "keywords_dict": keywords_dict}

        # 获取论文标题用于标题行
        title = paper_meta.title if paper_meta else paper.get("title", "Unknown")

        # 生成标题行
        status_label = self._paper_status_label(paper)
        title_with_status = f"{title[:100]}  `{status_label}`" if status_label else title[:100]
        if is_qualified_section:
            lines.append(f"### {idx}. {title_with_status}")
        else:
            status_icon = qualified_icon if score_resp.is_qualified else unqualified_icon
            title_with_status = f"{title}  `{status_label}`" if status_label else title
            lines.append(f"### {idx}. {status_icon} {title_with_status}")
        lines.append("")

        # 获取模块配置
        modules = self.basic_template.get("modules", [])

        # 使用渲染器工厂渲染各模块
        module_lines = self.renderer_factory.render_modules(paper_data, modules)
        lines.extend(module_lines)

        # 如果是及格论文部分，添加深度分析
        if is_qualified_section and analyses:
            paper_id = paper_meta.paper_id if paper_meta else paper.get("paper_id")
            analysis = next((a["analysis"] for a in analyses if a["paper_id"] == paper_id), None)
            if analysis:
                analysis_data = {"analysis": analysis}
                analysis_lines = self.renderer_factory.get_renderer("deep_analysis").render(
                    analysis_data, {}
                )
                lines.extend(analysis_lines)

        lines.append("---")
        lines.append("")

        return lines

    # ==================== HTML 报告生成 ====================

    def _get_report_css(self) -> str:
        """从 CSS 模板文件加载 HTML 报告样式，文件不存在时回退到内置样式"""
        return settings.load_report_css("html_report.css")

    @staticmethod
    def _h(text) -> str:
        """HTML 转义"""
        if text is None:
            return ""
        return html.escape(str(text))

    @staticmethod
    def _hm(text) -> str:
        """
        HTML 转义（保护 LaTeX 公式不被转义）。

        将 $$...$$ 和 $...$ 用占位符替换，对其他内容做 HTML 转义，
        再将占位符恢复为原始 LaTeX 内容，让 KaTeX 正确渲染。
        """
        import re
        if text is None:
            return ""
        text = str(text)
        # 提取所有 $$...$$ 和 $...$ 公式片段，用占位符替换
        placeholders = []

        def replace_display(m):
            placeholders.append(m.group(0))
            return f"\x00MATH{len(placeholders) - 1}\x00"

        def replace_inline(m):
            placeholders.append(m.group(0))
            return f"\x00MATH{len(placeholders) - 1}\x00"

        # 先处理块级公式（$$...$$），再处理行内公式（$...$）
        text = re.sub(r'\$\$.+?\$\$', replace_display, text, flags=re.DOTALL)
        text = re.sub(r'\$(?!\$).+?(?<!\$)\$', replace_inline, text)

        # 对非公式部分做 HTML 转义
        escaped = html.escape(text)

        # 恢复占位符（将占位符中的原始 LaTeX 直接嵌入，不转义）
        for i, placeholder_content in enumerate(placeholders):
            escaped = escaped.replace(f"\x00MATH{i}\x00", placeholder_content)

        return escaped

    def _generate_html_report(
        self,
        filepath: Path,
        source: str,
        display_name: str,
        papers: List[Dict[str, Any]],
        keywords_dict: Dict[str, float],
        analyses: List[Dict[str, Any]],
        has_deep_analysis: bool,
        all_paper_count: Optional[int] = None,
        qualified_count: Optional[int] = None,
        qualified_only: bool = False,
        token_usage: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """生成 HTML 格式报告"""
        html_path = filepath
        h = self._h

        today = datetime.now().strftime("%Y-%m-%d")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        total_papers = all_paper_count if all_paper_count is not None else len(papers)
        if qualified_count is None:
            qualified_count = sum(1 for p in papers if p["score_response"].is_qualified)
        displayed_count = len(papers)
        analyzed_count = len(analyses)

        total_weight = sum(keywords_dict.values())
        passing_score = settings.calculate_passing_score(total_weight)

        sorted_papers = sorted(papers, key=lambda x: x["score_response"].total_score, reverse=True)

        # 构建分析索引 {paper_id: analysis}
        analysis_map = {}
        for a in analyses:
            analysis_map[a["paper_id"]] = a["analysis"]

        parts = []
        parts.append("<!DOCTYPE html>")
        parts.append('<html lang="zh-CN"><head>')
        parts.append('<meta charset="UTF-8">')
        parts.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
        parts.append(f"<title>{h(display_name)} Report {today}</title>")
        parts.append(f"<style>{self._get_report_css()}</style>")
        # KaTeX 数学公式渲染
        parts.append(
            '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css" '
            'crossorigin="anonymous">'
        )
        parts.append(
            '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js" '
            'crossorigin="anonymous"></script>'
        )
        parts.append(
            '<script defer '
            'src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js" '
            'crossorigin="anonymous" '
            'onload="renderMathInElement(document.body, {'
            'delimiters: [{left:\'$$\',right:\'$$\',display:true},{left:\'$\',right:\'$\',display:false}]'
            '})"></script>'
        )
        parts.append("</head><body>")

        # 标题
        parts.append(f"<h1>{h(display_name)} Research Report</h1>")
        parts.append(
            f'<p class="meta">Generated: {h(timestamp)} | Passing score: {passing_score:.1f}</p>'
        )
        if qualified_only:
            parts.append(
                '<p class="meta">Showing qualified papers only: '
                f"{displayed_count}/{total_papers}. Other papers were scored and archived to avoid repeats.</p>"
            )

        # 统计栏
        parts.append('<div class="stats-bar">')
        parts.append(
            f'<div class="stat"><div class="num">{total_papers}</div><div class="label">Total</div></div>'
        )
        parts.append(
            f'<div class="stat"><div class="num">{qualified_count}</div><div class="label">Qualified</div></div>'
        )
        if has_deep_analysis:
            parts.append(
                f'<div class="stat"><div class="num">{analyzed_count}</div><div class="label">Analyzed</div></div>'
            )
        pct = (qualified_count / total_papers * 100) if total_papers else 0
        parts.append(
            f'<div class="stat"><div class="num">{pct:.0f}%</div><div class="label">Pass Rate</div></div>'
        )
        parts.append("</div>")

        # 论文卡片
        parts.append("<h2>Papers</h2>")
        if not sorted_papers and qualified_only:
            parts.append(
                '<p class="meta">No papers met the passing score. All candidates were still scored and archived.</p>'
            )
        for idx, paper in enumerate(sorted_papers, 1):
            sr = paper["score_response"]
            is_qual = sr.is_qualified
            cls = "pass" if is_qual else "fail"
            badge_text = "PASS" if is_qual else "FAIL"
            url = paper.get("url", "")
            title = paper.get("title", "Unknown")
            paper_meta = paper.get("paper_metadata")

            parts.append(f'<div class="card {cls}">')

            # 标题行
            status_label = self._paper_status_label(paper)
            status_html = f' <span class="revision-label">{h(status_label)}</span>' if status_label else ""
            if url:
                parts.append(
                    f'<div class="card-title"><a href="{h(url)}" target="_blank">{idx}. {h(title)}</a>{status_html}'
                )
            else:
                parts.append(f'<div class="card-title">{idx}. {h(title)}{status_html}')
            parts.append(f'<span class="badge {cls}">{badge_text}</span></div>')

            # 分数和元数据
            parts.append(
                f'<div class="field"><span class="field-label">Score:</span> '
                f'<span class="score">{sr.total_score:.1f}</span> / {passing_score:.1f}</div>'
            )
            authors = paper_meta.get_authors_string() if paper_meta else paper.get("authors", "")
            parts.append(
                f'<div class="field"><span class="field-label">Authors:</span> {h(authors)}</div>'
            )
            if paper_meta and paper_meta.published_date:
                published = paper_meta.published_date.strftime("%Y-%m-%d")
            else:
                published = paper.get("published", "")
            parts.append(
                f'<div class="field"><span class="field-label">Published:</span> {h(published)}</div>'
            )
            if paper_meta and paper_meta.version is not None:
                parts.append(
                    f'<div class="field"><span class="field-label">Version:</span> v{paper_meta.version}</div>'
                )

            # TLDR
            semantic_scholar_tldr = (
                getattr(paper_meta, "semantic_scholar_tldr", None)
                if paper_meta is not None
                else paper.get("semantic_scholar_tldr", "")
            )
            if semantic_scholar_tldr:
                parts.append(
                    '<div class="tldr"><strong>Semantic Scholar TL;DR:</strong> '
                    f"{self._hm(semantic_scholar_tldr)}</div>"
                )
            if sr.tldr and sr.tldr != "评分失败，无法生成摘要":
                parts.append(f'<div class="tldr"><strong>TL;DR:</strong> {self._hm(sr.tldr)}</div>')

            # 中文摘要（可折叠）
            abstract_cn = paper.get("abstract_cn", "")
            if abstract_cn:
                parts.append("<details open><summary>摘要翻译</summary>")
                parts.append(
                    f'<div class="analysis-content"><p>{self._hm(abstract_cn)}</p></div></details>'
                )

            # 摘要原文（可折叠）
            abstract = paper_meta.abstract if paper_meta else paper.get("abstract", "")
            if abstract:
                parts.append("<details><summary>Abstract</summary>")
                parts.append(f'<div class="analysis-content"><p>{self._hm(abstract)}</p></div></details>')

            # 评分详情（可折叠）
            if sr.keyword_scores:
                parts.append("<details><summary>评分详情</summary>")
                parts.append('<div class="analysis-content">')
                parts.append(
                    '<table style="width:100%;border-collapse:collapse;font-size:0.85em;">'
                )
                parts.append(
                    '<tr style="border-bottom:2px solid var(--color-border);">'
                    '<th style="text-align:left;padding:4px 8px;">关键词</th>'
                    '<th style="text-align:center;padding:4px 8px;">权重</th>'
                    '<th style="text-align:center;padding:4px 8px;">相关度</th>'
                    '<th style="text-align:center;padding:4px 8px;">得分</th></tr>'
                )
                for kw, score in sr.keyword_scores.items():
                    weight = keywords_dict.get(kw, 0)
                    weighted = score * weight
                    parts.append(
                        f'<tr style="border-bottom:1px solid var(--color-border);">'
                        f'<td style="padding:4px 8px;">{h(kw)}</td>'
                        f'<td style="text-align:center;padding:4px 8px;">{weight:.1f}</td>'
                        f'<td style="text-align:center;padding:4px 8px;">{score:.1f}/10</td>'
                        f'<td style="text-align:center;padding:4px 8px;">{weighted:.1f}</td></tr>'
                    )
                if sr.author_bonus > 0:
                    experts = ", ".join(sr.expert_authors_found)
                    parts.append(
                        f'<tr style="border-bottom:1px solid var(--color-border);">'
                        f'<td style="padding:4px 8px;">作者加分</td>'
                        f'<td style="text-align:center;padding:4px 8px;">-</td>'
                        f'<td style="text-align:center;padding:4px 8px;">+{sr.author_bonus:.1f}</td>'
                        f'<td style="text-align:center;padding:4px 8px;">专家: {h(experts)}</td></tr>'
                    )
                parts.append("</table>")
                if sr.reasoning:
                    parts.append(
                        f'<p style="margin-top:8px;"><strong>评分理由:</strong> {h(sr.reasoning)}</p>'
                    )
                parts.append("</div></details>")

            # 提取的关键词
            extracted_kw = (
                sr.extracted_keywords
                if hasattr(sr, "extracted_keywords") and sr.extracted_keywords
                else []
            )
            if extracted_kw:
                parts.append("<details><summary>关键词</summary>")
                parts.append(
                    f'<div class="analysis-content"><p>{h(", ".join(extracted_kw))}</p></div></details>'
                )

            # 深度分析（可折叠）
            paper_id = paper_meta.paper_id if paper_meta else paper.get("paper_id")
            analysis = analysis_map.get(paper_id)
            if analysis and isinstance(analysis, dict):
                parts.append("<details><summary>深度分析</summary>")
                parts.append('<div class="analysis-content">')
                for key, value in analysis.items():
                    if value is None:
                        continue
                    label = key.replace("_", " ").title()
                    if isinstance(value, list):
                        parts.append(f"<p><strong>{h(label)}:</strong></p><ul>")
                        for item in value:
                            parts.append(f"<li>{self._hm(str(item))}</li>")
                        parts.append("</ul>")
                    elif isinstance(value, dict):
                        parts.append(f"<p><strong>{h(label)}:</strong></p><ul>")
                        for k, v in value.items():
                            parts.append(f"<li><strong>{h(k)}:</strong> {self._hm(str(v))}</li>")
                        parts.append("</ul>")
                    else:
                        parts.append(f"<p><strong>{h(label)}:</strong> {self._hm(str(value))}</p>")
                parts.append("</div></details>")

            parts.append("</div>")  # card

        # Token 消耗统计
        if settings.TOKEN_TRACKING_ENABLED and token_usage and token_usage.get("has_data"):
            total = token_usage.get("total", 0)
            tp = token_usage.get("total_prompt", 0)
            tc = token_usage.get("total_completion", 0)
            parts.append(
                f'<p class="meta" style="margin-top:24px;border-top:1px solid #e5e7eb;padding-top:12px;">'
                f"Token 消耗: <strong>{total:,}</strong> tokens"
                f"（输入 {tp:,} / 输出 {tc:,}）</p>"
            )

        parts.append("</body></html>")

        try:
            self._write_text_atomic(html_path, "\n".join(parts))
            logger.info(f"[{source}] HTML 报告已生成: {html_path}")
            return html_path
        except Exception as e:
            logger.error(f"HTML 报告生成失败: {e}")
            raise ReportGenerationError(f"HTML 报告写入失败 ({html_path}): {e}") from e

    # ==================== 向后兼容接口 ====================

    def generate_comprehensive_report(
        self,
        all_papers_with_scores: List[Dict[str, Any]],
        keywords_dict: Dict[str, float],
        qualified_papers_with_analysis: List[Dict[str, Any]] = None,
    ):
        """
        生成综合研究报告（向后兼容接口）。

        此方法保留以支持旧版代码，新代码请使用 generate_reports_by_source()。
        """
        # 转换为新格式
        scored_papers_by_source = {"arxiv": all_papers_with_scores}
        analyses_by_source = {"arxiv": qualified_papers_with_analysis or []}

        self.generate_reports_by_source(
            scored_papers_by_source=scored_papers_by_source,
            keywords_dict=keywords_dict,
            analyses_by_source=analyses_by_source,
        )
