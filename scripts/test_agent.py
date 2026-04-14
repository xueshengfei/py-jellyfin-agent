"""测试 Jellyfin 媒体推荐 Agent"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import ask


def run_test(question: str):
    print("=" * 60)
    print(f"问题: {question}")
    print("-" * 60)
    try:
        answer = ask(question)
        print(f"回答:\n{answer}")
    except Exception as e:
        print(f"错误: {e}")
    print()


def main():
    tests = [
        "推荐2020到2025年的科幻电影",
        "我想看评分8分以上的动作片",
        "有什么好看的国产剧",
        "周杰伦有什么歌",
    ]

    for question in tests:
        run_test(question)

    print("========== 所有测试完成! ==========")


if __name__ == "__main__":
    main()
