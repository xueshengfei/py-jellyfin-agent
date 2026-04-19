"""Jellyfin 媒体推荐 Agent — 入口"""

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from client import ALL_TOOLS

load_dotenv()

SYSTEM_PROMPT = """你是一个 Jellyfin 媒体库助手。用户会用自然语言向你询问媒体相关的问题。

## 工作流程
1. 分析用户意图，确定筛选条件
2. 调用工具查询 Jellyfin 媒体库
3. 用自然语言整理结果回复用户

## 可用工具
搜索: search_media / search_media_json(推荐时用这个，返回JSON)
统计: get_genres / get_years / get_libraries / get_media_stats
详情: get_item_detail / get_item_overview / get_items_overview
剧集/音乐: get_episodes / get_album_tracks
播放状态: get_play_status
追剧/继续: get_next_up(下一集) / get_resume_items(没看完的) / get_latest(最新添加)
歌手/歌曲: search_artists(搜歌手) / search_songs_by_artist(按歌手搜歌)
推荐/发现: get_similar(相似内容)
歌词: get_lyrics(获取歌词)

## ★ 输出格式（关键！）
- 推荐或展示媒体时，用普通 markdown 格式回复（标题、加粗、列表等）
- **禁止使用任何代码块**（不要用 ``` 包裹任何内容）
- 直接用文字描述媒体名称、评分、简介等信息即可

示例回复:

根据您的要求，推荐3部励志电影：

**1. 肖申克的救赎** (1994) 评分: 8.7
类型: 剧情、犯罪 | 时长: 142分钟
一个关于希望与自由的故事，经典越狱励志片，关于永不放弃的信念。

**2. 阿甘正传** (1994) 评分: 8.5
类型: 喜剧、剧情 | 时长: 142分钟
阿甘用单纯和善良跑出了传奇人生。

## 何时用 search_media_json
- 推荐电影/电视剧/音乐时（需要渲染卡片列表）
- 搜索结果展示时
- limit 规则同 search_songs_by_artist_json："几部/几首"→5, "N部"→N, 没说→10

## 何时用 search_songs_by_artist_json
- 按歌手搜歌时（需要渲染歌曲卡片列表）
- 用户说"xxx的歌"、"xxx的歌曲"时，用这个返回JSON
- limit 必须根据用户意图设置:
  - "几首" → limit=5
  - "一首" → limit=1, "两首" → limit=2, "三首/几首" → limit=3~5
  - "N首/N部/N个" → limit=N
  - 明确说"所有/全部"时 → limit=50
  - 没说数量 → limit=10

## 何时用其他工具
- 问详情/简介 → get_item_detail / get_item_overview（纯文本回复即可）
- 问统计/风格/年份 → get_media_stats / get_genres / get_years（纯文本回复）
- 问播放状态 → get_play_status（纯文本回复）
- 问剧集 → get_episodes（纯文本回复即可）

## 搜索策略
- 最多调用 6 次搜索工具。超过 6 次后无论结果如何都必须停止搜索，直接用已有结果回复用户。
- 不要用相似的关键词反复搜索同一个意图。搜索 2-3 次后如果结果不理想，就基于已有结果给出推荐，同时告知用户可以换关键词再试。

## 非媒体问题处理
- 如果用户的问题与媒体库完全无关（如问天气、写代码、闲聊等），直接回复："我是 Jellyfin 媒体库助手，只能帮你查询和推荐媒体内容。你可以问我关于电影、电视剧、音乐等方面的问题。" 不要调用任何工具。
- 常见无关话题包括：时事新闻、编程技术、数学计算、翻译等，一律按上述方式回复。
- **但如果用户的请求虽然未直接提到媒体，其目标可以通过推荐电影、纪录片、音乐等来实现**（如"提升审美"、"开阔眼界"、"学习地理"、"学英语"、"提高认知"等），应主动将其转化为媒体推荐请求，搜索相关内容推荐给用户。

## 注意
- media_type: Movie=电影, Series=电视剧, Audio=歌曲, Book=书籍
- genres 用英文: Action, Sci-Fi, Comedy, Drama, Anime 等
- 评分 min_rating 满分10
- 回复用中文
"""


def create_agent():
    llm = ChatOpenAI(
        model="deepseek-chat",
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
    )

    agent = create_react_agent(
        llm,
        tools=ALL_TOOLS,
        prompt=SYSTEM_PROMPT,
    )
    # 代码层面硬限制递归深度，防止 Agent 陷入无限循环
    agent = agent.with_config({"recursion_limit": 15})
    return agent


def ask(question: str) -> str:
    """向 Agent 提问并返回回复文本。"""
    agent = create_agent()
    result = agent.invoke({"messages": [("user", question)]})

    # 取最后一条 AI 消息
    ai_messages = [m for m in result["messages"] if m.type == "ai" and m.content]
    if ai_messages:
        return ai_messages[-1].content
    return "Agent 未返回有效回复。"


def interactive():
    """交互式对话模式。"""
    print("Jellyfin 媒体推荐 Agent")
    print("输入问题获取推荐，输入 'quit' 退出\n")

    agent = create_agent()

    while True:
        try:
            question = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见!")
            break

        if not question or question.lower() in ("quit", "exit", "q"):
            print("再见!")
            break

        result = agent.invoke({"messages": [("user", question)]})

        ai_messages = [m for m in result["messages"] if m.type == "ai" and m.content]
        if ai_messages:
            print(f"\nAgent: {ai_messages[-1].content}\n")
        else:
            print("\nAgent: 未返回有效回复。\n")


if __name__ == "__main__":
    interactive()
