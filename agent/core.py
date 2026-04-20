"""Jellyfin 媒体推荐 Agent — 入口"""

import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from client import ALL_TOOLS

load_dotenv()

SYSTEM_PROMPT_TEMPLATE = """你是一个 Jellyfin 媒体库助手。用户会用自然语言向你询问媒体相关的问题。

## 媒体库概况
{library_context}

## 工作流程
1. 分析用户意图，确定筛选条件（参考上方媒体库概况选择正确的 genres 和 media_type）
2. 调用工具查询 Jellyfin 媒体库
3. 用自然语言整理结果回复用户

## 可用工具
搜索: search_media / search_media_json(推荐时用这个，返回JSON)
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

## 搜索策略
- 推荐类请求只需调用 1 次 search_media_json，用最精准的关键词即可
- genres 参数必须使用上方风格列表中的原值（中英文均可，优先用列表中存在的值）
- 如果第一次搜索无结果，最多再试 1 次不同关键词，然后基于已有结果回复
- 不要调用 get_genres / get_years / get_media_stats，这些信息已在上方媒体库概况中提供

## limit 规则
- "几部/几首"→5, "N部"→N, 没说→10, "所有/全部"→50
- 按歌手搜歌时用 search_songs_by_artist

## 非媒体问题处理
- 与媒体库完全无关的问题，直接回复："我是 Jellyfin 媒体库助手，只能帮你查询和推荐媒体内容。你可以问我关于电影、电视剧、音乐等方面的问题。" 不要调用任何工具。
- 但如果用户的请求虽然未直接提到媒体，其目标可以通过推荐电影、纪录片、音乐等来实现（如"提升审美"、"开阔眼界"、"学习地理"、"学英语"等），应主动搜索相关内容推荐。

## 注意
- media_type: Movie=电影, Series=电视剧, Audio=歌曲, Book=书籍
- 评分 min_rating 满分10
- 回复用中文"""


def _build_library_context() -> str:
    """从缓存构建媒体库概况文本，注入到 System Prompt。"""
    from client.jellyfin import _cache

    if not _cache:
        return "（缓存未加载，请调用工具获取）"

    lines = []

    # 统计数量
    stats = _cache.get("stats")
    if stats:
        stat_parts = [f"{k}{v}" for k, v in stats.items() if v and v > 0]
        if stat_parts:
            lines.append("数量: " + ", ".join(stat_parts))

    # 媒体库
    libraries = _cache.get("libraries")
    if libraries:
        lib_names = [lib["name"] for lib in libraries]
        lines.append("媒体库: " + ", ".join(lib_names))

    # 风格标签（完整列表，LLM 用这些值作为 genres 参数）
    genres = _cache.get("genres")
    if genres:
        lines.append("风格标签（genres 参数只能用以下值）:")
        # 分行显示，每行约 10 个
        for i in range(0, len(genres), 10):
            chunk = genres[i:i + 10]
            lines.append("  " + ", ".join(chunk))

    # 年份范围
    years = _cache.get("years")
    if years:
        years_str = [str(y) for y in years]
        lines.append(f"年份范围: {years_str[-1]}~{years_str[0]}（共 {len(years)} 个年份）")

    return "\n".join(lines)


def create_agent():
    llm = ChatOpenAI(
        model="deepseek-chat",
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
    )

    prompt = SYSTEM_PROMPT_TEMPLATE.format(library_context=_build_library_context())
    agent = create_react_agent(
        llm,
        tools=ALL_TOOLS,
        prompt=prompt,
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
