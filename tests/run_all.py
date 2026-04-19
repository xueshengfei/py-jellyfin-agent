"""批量运行测试用例并生成报告。

用法:
  python tests/run_all.py              # 跑全部 100 条
  python tests/run_all.py --limit 20   # 只跑 20 条
  python tests/run_all.py --category 搜索推荐  # 只跑某个分类
  python tests/run_all.py --concurrency 5       # 调整并发数
"""

import argparse
import json
import os
import sys
import time

import httpx

BASE_URL = os.getenv("AGENT_URL", "http://localhost:5000")
CASES_FILE = os.path.join(os.path.dirname(__file__), "test_cases.json")


def load_cases(path: str) -> dict[str, list[str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["test_cases"]


def run_benchmark(questions: list[str], concurrency: int, run_id: str = "") -> dict:
    resp = httpx.post(
        f"{BASE_URL}/debug/benchmark",
        json={"questions": questions, "concurrency": concurrency, "repeat": 1, "run_id": run_id},
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="批量运行测试用例")
    parser.add_argument("--limit", type=int, default=0, help="每个分类最多取 N 条，0=全部")
    parser.add_argument("--category", type=str, default="", help="只跑指定分类")
    parser.add_argument("--concurrency", type=int, default=3, help="并发数（默认 3）")
    parser.add_argument("--batch-size", type=int, default=10, help="每批条数（默认 10）")
    args = parser.parse_args()

    cases = load_cases(CASES_FILE)
    total_cases = sum(len(v) for v in cases.values())
    print(f"测试用例文件: {CASES_FILE}")
    print(f"共 {len(cases)} 个分类, {total_cases} 条用例")
    print(f"并发数: {args.concurrency} | 批大小: {args.batch_size}")
    print()

    all_reports = []
    all_questions = []

    # 生成 run_id，同一次运行的所有报告放入同一子目录
    run_id = time.strftime("%Y%m%d_%H%M%S")
    print(f"运行 ID: {run_id}")
    print()

    for cat, questions in cases.items():
        if args.category and cat != args.category:
            continue
        if args.limit > 0:
            questions = questions[:args.limit]

        print(f"[{cat}] {len(questions)} 条用例...")

        # 分批跑，避免一次性发太多
        for i in range(0, len(questions), args.batch_size):
            batch = questions[i : i + args.batch_size]
            print(f"  批次 {i // args.batch_size + 1}: {batch[0]} 等 {len(batch)} 条...")
            try:
                report = run_benchmark(batch, args.concurrency, run_id=run_id)
                report_file = report.get("report_file", "")
                wall = report["wall_time_ms"] / 1000
                errors = report["summary"]["errors_total"]
                print(f"  完成: {wall:.1f}s, 错误={errors}, 报告={report_file}")

                for r in report["results"]:
                    avg = r["avg_ms"] / 1000
                    print(f"    - {r['question']}: {avg:.1f}s ({r['category']})")

                all_reports.append(report)
                all_questions.extend(batch)
            except Exception as e:
                print(f"  失败: {e}")

    print()
    print("=" * 50)
    print(f"全部完成: {len(all_questions)} 条用例")
    print(f"生成报告: {len(all_reports)} 份")

    # 汇总统计
    cat_stats: dict[str, dict] = {}
    for report in all_reports:
        for r in report["results"]:
            cat = r["category"]
            if cat not in cat_stats:
                cat_stats[cat] = {"count": 0, "total_ms": 0}
            cat_stats[cat]["count"] += 1
            cat_stats[cat]["total_ms"] += r["avg_ms"]

    print()
    print("分类汇总:")
    for cat, s in cat_stats.items():
        avg = s["total_ms"] / s["count"] / 1000 if s["count"] else 0
        print(f"  {cat}: {s['count']} 条, 平均 {avg:.1f}s")


if __name__ == "__main__":
    main()
