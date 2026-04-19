"""Debug 追踪与 Benchmark 工具。"""

import asyncio
import json
import logging
import os
import time

from agent import create_agent

logger = logging.getLogger("jellyfin-agent.debug")

# 确保 debug 日志输出到文件
_DEBUG_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug.log")


class DebugTrace:
    """请求级计时上下文，记录各阶段耗时。

    enabled=False 时所有方法为空操作，零开销。
    """

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self._phases: list[dict] = []
        self._active: dict[str, float] = {}
        self._t0: float = 0
        self.question: str = ""
        self.token_count: int = 0
        self.tool_calls: list[str] = []
        self.card_count: int = 0
        self.response: str = ""
        self.error: str | None = None

    # ── 计时 ──────────────────────────────────────────

    def start(self, phase: str) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        if not self._t0:
            self._t0 = now
        self._active[phase] = now

    def end(self, phase: str, **meta) -> None:
        if not self.enabled:
            return
        t0 = self._active.pop(phase, None)
        if t0 is None:
            return
        ms = round((time.perf_counter() - t0) * 1000, 1)
        self._phases.append({"phase": phase, "ms": ms, **meta})

    def close_remaining(self) -> None:
        """关闭所有未结束的 phase。"""
        if not self.enabled:
            return
        now = time.perf_counter()
        for phase, t0 in list(self._active.items()):
            ms = round((now - t0) * 1000, 1)
            self._phases.append({"phase": phase, "ms": ms})
        self._active.clear()

    # ── 输出 ──────────────────────────────────────────

    def log(self) -> None:
        """格式化输出到控制台 + 日志文件。"""
        if not self.enabled or not self._t0:
            return
        total = round((time.perf_counter() - self._t0) * 1000, 1)
        sep = "─" * 50
        lines = [
            f"\n[DEBUG] {sep}",
            f'[DEBUG] 问题: "{self.question}"',
            f"[DEBUG] 总耗时: {total}ms",
        ]
        for p in self._phases:
            extras = []
            for k in ("tokens", "tool", "cards"):
                if k in p:
                    extras.append(f"{k}: {p[k]}")
            extra = f"  ({', '.join(extras)})" if extras else ""
            lines.append(f"[DEBUG]   {p['phase']:<25} {p['ms']:>7.1f}ms{extra}")
        if self.error:
            lines.append(f"[DEBUG]   错误: {self.error}")
        lines.append(f"[DEBUG] {sep}")

        msg = "\n".join(lines)
        # 输出到 logger（uvicorn 能捕获）
        logger.info(msg)
        # 同时写文件兜底
        with open(_DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(msg + "\n")

    def to_dict(self) -> dict:
        """转为字典（给 benchmark 报告用）。"""
        total = round((time.perf_counter() - self._t0) * 1000, 1) if self._t0 else 0
        return {
            "question": self.question,
            "total_ms": total,
            "phases": self._phases,
            "tool_calls": self.tool_calls,
            "token_count": self.token_count,
            "card_count": self.card_count,
            "response": self.response,
            "error": self.error,
        }


# ── Benchmark 核心逻辑 ─────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "搜索推荐": ["推荐", "找", "搜", "哪部", "几部", "好看", "高分"],
    "详情查询": ["讲了什么", "详情", "简介", "什么内容", "详细信息", "剧情"],
    "音乐查询": ["歌", "歌手", "专辑", "歌词", "音乐", "唱"],
    "播放状态": ["看到哪", "没看完", "下一集", "继续看", "播放", "追剧"],
    "无关问题": ["天气", "写代码", "翻译", "算", "数学", "编程", "新闻"],
}


def categorize(question: str) -> str:
    """根据问题关键词自动分类。"""
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in question for kw in keywords):
            return cat
    return "其他"


async def run_single(question: str, semaphore: asyncio.Semaphore) -> DebugTrace:
    """跑单次 Agent 请求，返回带计时的 DebugTrace。"""
    async with semaphore:
        trace = DebugTrace(enabled=True)
        trace.question = question

        try:
            trace.start("agent_create")
            agent = create_agent()
            trace.end("agent_create")

            full_answer = ""
            collected_items: dict = {}
            llm_count = 0
            current_llm = ""
            llm_tokens = 0

            async for event in agent.astream_events(
                {"messages": [("user", question)]},
                version="v2",
            ):
                kind = event["event"]
                name = event.get("name", "")

                if kind == "on_chat_model_start":
                    if current_llm:
                        trace.end(current_llm, tokens=llm_tokens)
                        trace.token_count += llm_tokens
                    llm_count += 1
                    current_llm = f"llm_{llm_count}"
                    llm_tokens = 0
                    trace.start(current_llm)

                elif kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if isinstance(chunk.content, str) and chunk.content:
                        full_answer += chunk.content
                        llm_tokens += 1

                elif kind == "on_tool_start":
                    if current_llm:
                        trace.end(current_llm, tokens=llm_tokens)
                        trace.token_count += llm_tokens
                        current_llm = ""
                    trace.start(f"tool:{name}")

                elif kind == "on_tool_end":
                    trace.end(f"tool:{name}")
                    trace.tool_calls.append(name)
                    output = event["data"].get("output", "")
                    output_str = output.content if hasattr(output, "content") else str(output)
                    if name in ("search_media_json", "search_songs_by_artist_json") and output_str:
                        try:
                            items = json.loads(output_str)
                            for item in items:
                                if item.get("id"):
                                    collected_items[item["id"]] = item
                        except (json.JSONDecodeError, TypeError):
                            pass

            if current_llm:
                trace.end(current_llm, tokens=llm_tokens)
                trace.token_count += llm_tokens

            trace.card_count = len(collected_items)
            trace.response = full_answer

        except Exception as e:
            trace.error = str(e)
            trace.close_remaining()

        return trace


def build_report(
    traces: list[DebugTrace],
    questions: list[str],
    concurrency: int,
    repeat: int,
    wall_time: float,
) -> dict:
    """从 traces 构建 benchmark 报告。"""
    total_runs = len(traces)

    # 按问题分组
    question_results: dict[str, dict] = {}
    for trace in traces:
        q = trace.question
        if q not in question_results:
            question_results[q] = {
                "question": q,
                "category": categorize(q),
                "runs": [],
            }
        td = trace.to_dict()
        question_results[q]["runs"].append({
            "total_ms": td.get("total_ms", 0),
            "phases": td.get("phases", []),
            "tool_calls": td.get("tool_calls", []),
            "token_count": td.get("token_count", 0),
            "card_count": td.get("card_count", 0),
            "response": td.get("response", ""),
            "error": td.get("error"),
        })

    # 每个问题的统计
    results = []
    by_category: dict[str, dict] = {}

    for q, data in question_results.items():
        runs = data["runs"]
        successful = [r for r in runs if not r.get("error")]
        error_count = len(runs) - len(successful)
        ms_list = [r["total_ms"] for r in successful] if successful else [0]

        entry = {
            "question": q,
            "category": data["category"],
            "runs": runs,
            "avg_ms": round(sum(ms_list) / len(ms_list), 1) if ms_list else 0,
            "min_ms": min(ms_list) if ms_list else 0,
            "max_ms": max(ms_list) if ms_list else 0,
            "errors": error_count,
        }
        results.append(entry)

        cat = data["category"]
        if cat not in by_category:
            by_category[cat] = {"total_ms": [], "count": 0}
        by_category[cat]["total_ms"].extend(ms_list)
        by_category[cat]["count"] += len(runs)

    # 分类汇总
    category_summary = {}
    for cat, data in by_category.items():
        ms = data["total_ms"]
        category_summary[cat] = {
            "avg_ms": round(sum(ms) / len(ms), 1) if ms else 0,
            "count": data["count"],
        }

    # 最慢阶段
    phase_totals: dict[str, float] = {}
    for q_data in results:
        for run in q_data["runs"]:
            for phase in run.get("phases", []):
                name = phase["phase"]
                phase_totals[name] = phase_totals.get(name, 0) + phase["ms"]

    return {
        "config": {"concurrency": concurrency, "repeat": repeat, "total_runs": total_runs},
        "wall_time_ms": wall_time,
        "results": results,
        "summary": {
            "by_category": category_summary,
            "slowest_phase": max(phase_totals, key=phase_totals.get) if phase_totals else None,
            "errors_total": sum(r["errors"] for r in results),
        },
    }


# ── 报告保存 ──────────────────────────────────────────

_TESTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tests")
_TEST_OUTPUT_DIR = os.path.join(_TESTS_DIR, "test-output")


def _fmt_ms(ms: float) -> str:
    """毫秒格式化。"""
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms:.0f}ms"


def save_report(report: dict, run_id: str = "") -> str:
    """将 benchmark 报告保存为 Markdown 表格文件，返回文件路径。

    run_id: 同一次测试运行的标识，所有批次报告存入同一子目录。
            为空时自动按当前时间生成。
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if not run_id:
        run_id = timestamp

    output_dir = os.path.join(_TEST_OUTPUT_DIR, run_id)
    os.makedirs(output_dir, exist_ok=True)

    filepath = os.path.join(output_dir, f"benchmark_{timestamp}.md")

    config = report["config"]
    summary = report.get("summary", {})
    by_category = summary.get("by_category", {})

    lines = [
        "# Benchmark Report",
        "",
        f"**时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**并发数**: {config['concurrency']} | **重复次数**: {config['repeat']} | **总运行**: {config['total_runs']} 次  ",
        f"**总耗时**: {_fmt_ms(report['wall_time_ms'])}",
        "",
    ]

    # ── 汇总表 ──
    if by_category:
        lines += [
            "## 汇总",
            "",
            "| 分类 | 平均耗时 | 最慢 | 最快 | 请求数 | 错误数 |",
            "|------|---------|------|------|--------|--------|",
        ]
        for q_data in report["results"]:
            cat = q_data["category"]
            errors = q_data["errors"]
            if cat in by_category:
                lines.append(
                    f"| {cat} | {_fmt_ms(q_data['avg_ms'])} "
                    f"| {_fmt_ms(q_data['max_ms'])} "
                    f"| {_fmt_ms(q_data['min_ms'])} "
                    f"| {len(q_data['runs'])} | {errors} |"
                )
        lines.append("")

    if summary.get("slowest_phase"):
        lines.append(f"> 最慢阶段: `{summary['slowest_phase']}`  ")
        lines.append(f"> 错误总数: {summary.get('errors_total', 0)}")
        lines.append("")

    # ── 详细表 ──
    lines += [
        "## 详细结果",
        "",
        "| # | 测试用例 | 分类 | agent_create | llm_1 | 工具调用 | llm_2 | card_match | card_reason | 总耗时 | tokens | 卡片数 | 错误 |",
        "|:---:|---------|------|:-----------:|:-----:|:-------:|:-----:|:---------:|:----------:|:------:|:------:|:------:|:----:|",
    ]

    row_num = 0
    responses = []  # 收集回复，表格下方展示
    for q_data in report["results"]:
        for run_idx, run in enumerate(q_data["runs"]):
            row_num += 1
            phases = {p["phase"]: p for p in run.get("phases", [])}

            def cell(name: str) -> str:
                p = phases.get(name)
                if not p:
                    return "-"
                extra = f"({p['tokens']} tokens)" if "tokens" in p else ""
                return f"{_fmt_ms(p['ms'])} {extra}".strip()

            # 工具调用列
            tool_calls = run.get("tool_calls", [])
            if tool_calls:
                parts = []
                for tc in tool_calls:
                    p = phases.get(f"tool:{tc}")
                    if p:
                        parts.append(f"{tc}<br>{_fmt_ms(p['ms'])}")
                tool_str = "<br>".join(parts) if parts else "-"
            else:
                tool_str = "-"

            error = run.get("error") or "-"

            lines.append(
                f"| {row_num} | {q_data['question']} | {q_data['category']} "
                f"| {cell('agent_create')} | {cell('llm_1')} | {tool_str} "
                f"| {cell('llm_2')} | {cell('card_match')} | {cell('card_reason')} "
                f"| **{_fmt_ms(run['total_ms'])}** | {run.get('token_count', 0)} "
                f"| {run.get('card_count', 0)} | {error} |"
            )

            # 收集回复（只取第一次 run 的）
            if run_idx == 0 and run.get("response"):
                responses.append((row_num, q_data["question"], run["response"]))

        # 平均行（repeat > 1 时显示）
        if len(q_data["runs"]) > 1:
            lines.append(
                f"| | *{q_data['question']} 平均* | | | | | | | | "
                f"*{_fmt_ms(q_data['avg_ms'])}* | | | |"
            )

    lines.append("")

    # ── LLM 回复 ──
    if responses:
        lines.append("## LLM 回复")
        lines.append("")
        for idx, question, resp in responses:
            lines.append(f"### {idx}. {question}")
            lines.append("")
            lines.append(resp.strip())
            lines.append("")
            lines.append("---")
            lines.append("")

    content = "\n".join(lines)

    # 写文件
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    # 同时输出到控制台
    logger.info(f"\n{content}")

    # 写到 debug.log
    with open(_DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(content + "\n")

    return filepath
