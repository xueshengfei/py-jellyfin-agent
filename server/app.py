"""Jellyfin Agent HTTP 服务 — 参考 deer-flow 架构"""

import asyncio
import json
import os
import time
import traceback
import uuid
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from pydantic import BaseModel

from agent import create_agent, SYSTEM_PROMPT
from server.debug import DebugTrace, run_single, build_report, save_report
from client import (
    search_media, get_genres, get_years, get_libraries, get_media_stats,
    get_item_detail, get_item_overview, get_items_overview,
    get_episodes, get_album_tracks, get_play_status,
    search_items_raw, warm_cache, refresh_cache,
    get_next_up, get_resume_items, get_latest, search_artists,
    search_songs_by_artist, get_similar, get_lyrics,
    get_next_up_raw, get_resume_items_raw, get_latest_raw, search_artists_raw,
)

load_dotenv()

app = FastAPI(title="Jellyfin Media Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    warm_cache()


@app.post("/refresh_cache")
def api_refresh_cache():
    """手动刷新缓存。"""
    refresh_cache()
    return {"status": "ok"}

# ── 会话管理 ────────────────────────────────────────────
# session_id → {"messages": [HumanMessage/AIMessage, ...]}
_sessions: dict[str, dict] = {}
MAX_SESSIONS = 100
MAX_HISTORY = 20  # 每个会话最多保留 10 轮对话（每轮 user+ai = 2 条消息）


def get_or_create_session(session_id: str | None) -> tuple[str, dict]:
    """获取或创建会话，返回 (session_id, session_data)。"""
    if session_id and session_id in _sessions:
        return session_id, _sessions[session_id]
    # 新建会话
    sid = session_id or str(uuid.uuid4())[:8]
    _sessions[sid] = {"messages": []}
    # 淘汰旧会话
    if len(_sessions) > MAX_SESSIONS:
        oldest = list(_sessions.keys())[0]
        del _sessions[oldest]
    return sid, _sessions[sid]


# ── SSE 工具 ──────────────────────────────────────────────

def sse_event(event: str, data: any) -> str:
    """格式化一条 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _match_final_items(answer: str, collected: dict) -> list[dict]:
    """根据 LLM 回复中提到的媒体名称匹配最终推荐项。

    按名称长度降序匹配（长名优先），避免 "教父" 误匹配 "教父2"。
    匹配后从文本中移除已匹配名称，防止子串重复命中。
    """
    items = sorted(collected.values(), key=lambda x: len(x.get("name", "")), reverse=True)
    final = []
    remaining = answer
    for item in items:
        name = item.get("name", "")
        if name and name in remaining:
            final.append(item)
            remaining = remaining.replace(name, "", 1)
    # 按名称在原文中的出现顺序排列
    final.sort(key=lambda x: answer.find(x.get("name", "")))
    return final


# ── Models ─────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    debug: bool = False


class BenchmarkRequest(BaseModel):
    questions: list[str]
    concurrency: int = 3
    repeat: int = 1
    run_id: str = ""


# ── LLM Agent 接口 ────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/sessions")
def list_sessions():
    """列出所有活跃会话。"""
    return {
        "sessions": [
            {"id": sid, "messages": len(s["messages"])}
            for sid, s in _sessions.items()
        ]
    }


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    """删除会话。"""
    if session_id in _sessions:
        del _sessions[session_id]
        return {"status": "deleted"}
    return JSONResponse(status_code=404, content={"error": "会话不存在"})


@app.post("/ask_stream")
async def ask_stream_post(req: AskRequest):
    """POST 版本，JSON body 传参。"""
    return _build_sse_response(req.question, req.session_id, debug=req.debug)


@app.get("/ask_stream")
async def ask_stream_get(question: str, session_id: Optional[str] = None, debug: bool = False):
    """GET 版本，query string 传参（兼容浏览器 EventSource）。

    用法: GET /ask_stream?question=推荐3部电影&session_id=可选&debug=true
    """
    return _build_sse_response(question, session_id, debug=debug)


def _build_sse_response(question: str, session_id: Optional[str], debug: bool = False):
    session_id, session = get_or_create_session(session_id)
    trace = DebugTrace(enabled=debug)
    trace.question = question

    async def generate():
        trace.start("agent_create")
        agent = create_agent()
        trace.end("agent_create")
        full_answer = ""
        collected_items = {}
        llm_count = 0
        current_llm = ""
        llm_tokens = 0

        # 拼接历史消息 + 当前问题
        history = list(session["messages"])
        input_messages = history + [("user", question)]

        # ★ 用 astream_events 实现 token 级流式
        async for event in agent.astream_events(
            {"messages": input_messages},
            version="v2",
        ):
            kind = event["event"]
            name = event.get("name", "")

            # LLM 开始生成
            if kind == "on_chat_model_start":
                if current_llm:
                    trace.end(current_llm, tokens=llm_tokens)
                    trace.token_count += llm_tokens
                llm_count += 1
                current_llm = f"llm_{llm_count}"
                llm_tokens = 0
                trace.start(current_llm)
                yield sse_event("thinking", {"node": "llm"})
                await asyncio.sleep(0)

            # ★ LLM 逐 token 输出
            elif kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                if isinstance(chunk.content, str) and chunk.content:
                    full_answer += chunk.content
                    llm_tokens += 1
                    yield sse_event("token", {"content": chunk.content})
                    await asyncio.sleep(0)

            # 工具调用开始（不推送客户端，仅内部处理）
            elif kind == "on_tool_start":
                if current_llm:
                    trace.end(current_llm, tokens=llm_tokens)
                    trace.token_count += llm_tokens
                    current_llm = ""
                trace.start(f"tool:{name}")
                await asyncio.sleep(0)

            # 工具调用结束（收集数据，不推送给客户端）
            elif kind == "on_tool_end":
                trace.end(f"tool:{name}")
                trace.tool_calls.append(name)
                output = event["data"].get("output", "")
                output_str = output.content if hasattr(output, "content") else str(output)

                # 收集 search_media_json / search_songs_by_artist_json 结果
                if name in ("search_media_json", "search_songs_by_artist_json") and output_str:
                    try:
                        items = json.loads(output_str)
                        for item in items:
                            if item.get("id"):
                                collected_items[item["id"]] = item
                    except (json.JSONDecodeError, TypeError):
                        pass

                await asyncio.sleep(0)

        # 结束最后一个 LLM phase
        if current_llm:
            trace.end(current_llm, tokens=llm_tokens)
            trace.token_count += llm_tokens

        # 流结束后，从回答中提取最终推荐，生成推荐理由，推送精简卡片
        trace.start("card_match")
        final_cards = _match_final_items(full_answer, collected_items)
        trace.end("card_match", cards=len(final_cards))

        if final_cards:
            trace.start("card_reason")
            yield sse_event("thinking", {"node": "reason"})
            await asyncio.sleep(0)
            try:
                items_brief = json.dumps([
                    {"i": i, "name": c.get("name"), "year": c.get("year"),
                     "rating": c.get("rating"), "genres": c.get("genres"),
                     "overview": (c.get("overview") or "")[:80]}
                    for i, c in enumerate(final_cards)
                ], ensure_ascii=False)
                reason_resp = get_llm().invoke([
                    {"role": "system", "content": REASON_PROMPT.format(
                        question=question, items_json=items_brief)},
                    {"role": "user", "content": question},
                ])
                reason_raw = reason_resp.content.strip()
                if reason_raw.startswith("```"):
                    reason_raw = reason_raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                reasons = json.loads(reason_raw)
                reason_map = {r.get("index"): r.get("reason", "") for r in reasons}
                for i, card in enumerate(final_cards):
                    card["reason"] = reason_map.get(i, "")
                    yield sse_event("card", {"id": card["id"], "reason": card["reason"], "type": _card_type(card.get("type", ""))})
                    await asyncio.sleep(0)
            except Exception:
                pass
            trace.end("card_reason", cards=len(final_cards))

        # 保存到会话历史（只保留 user + ai 文本，不存工具调用）
        session["messages"].append(("user", question))
        if full_answer:
            session["messages"].append(("ai", full_answer))
        # 裁剪历史
        if len(session["messages"]) > MAX_HISTORY:
            session["messages"] = session["messages"][-MAX_HISTORY:]

        trace.response = full_answer
        trace.log()

        yield sse_event("session", {"session_id": session_id, "history_count": len(session["messages"])})
        await asyncio.sleep(0)
        yield sse_event("done", {"answer": full_answer, "cards": [{"id": c["id"], "reason": c.get("reason", ""), "type": _card_type(c.get("type", ""))} for c in final_cards], "session_id": session_id})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── LLM 意图分析接口 ─────────────────────────────────────

INTENT_PROMPT = """你是一个意图分析器。根据用户的自然语言输入，判断应该调用哪个工具及参数。

可用工具及参数（参数名必须严格一致）:

1. search_media — 搜索/推荐媒体
   参数: keyword(str), media_type(str: Movie/Series/Audio/Book), genres(str,英文逗号分隔), min_year(int), max_year(int), min_rating(float), max_rating(float), limit(int,默认20), sort_by(str: CommunityRating/DateCreated/SortName/ProductionYear), sort_order(str: Descending/Ascending)

2. get_item_detail — 获取单个条目完整元数据（详情、演员、导演）
   参数: keyword(str) 或 item_id(str)

3. get_item_overview — 获取单个条目简介
   参数: keyword(str) 或 item_id(str)

4. get_items_overview — 批量获取多条简介
   参数: keyword(str), media_type(str), genres(str), min_year(int), max_year(int), min_rating(float), limit(int,默认10), sort_by(str), sort_order(str)

5. get_episodes — 获取电视剧剧集列表
   参数: series_keyword(str), season_number(int,默认1)

6. get_album_tracks — 获取专辑歌曲列表
   参数: keyword(str)

7. get_play_status — 获取播放状态
   参数: media_type(str), keyword(str), status_filter(str: all/unplayed/played/favorite), limit(int)

8-11: get_genres / get_years / get_libraries / get_media_stats — 无参数, args={}

12. get_next_up — 获取下一集列表（追剧）
   参数: limit(int,默认10)

13. get_resume_items — 获取没看完的媒体
   参数: media_type(str: Video/Audio,默认Video), limit(int,默认10)

14. get_latest — 获取最新添加的内容
   参数: media_type(str: Movie/Series/Audio/Book), limit(int,默认10)

15. search_artists — 搜索歌手
   参数: keyword(str), limit(int,默认20)

16. search_songs_by_artist — 按歌手名搜歌
   参数: artist_name(str), limit(int,默认20)

17. get_similar — 获取相似内容
   参数: keyword(str) 或 item_id(str), limit(int,默认10)

18. get_lyrics — 获取歌词
   参数: keyword(str) 或 item_id(str)

规则:
- 必须返回 JSON: {"tool": "工具名", "args": {参数}}
- 不需要的参数不要写，不要写 null
- media_type: 电影→Movie, 电视剧→Series, 歌曲→Audio, 书→Book
- genres 用英文: 科幻→Sci-Fi, 动作→Action, 喜剧→Comedy, 恐怖→Horror, 动画→Anime, 爱情→Romance, 悬疑→Mystery, 犯罪→Crime, 纪录片→Documentary, 剧情→Drama, 冒险→Adventure, 奇幻→Fantasy, 战争→War, 励志→Drama
- sort_by 只能是: CommunityRating, DateCreated, SortName, ProductionYear
- sort_order 只能是: Descending 或 Ascending
- 用户说"xxx有什么歌"→ get_album_tracks(keyword="xxx")
- 用户说"xxx讲了什么"→ get_item_overview(keyword="xxx")
- 用户说"xxx的详情"→ get_item_detail(keyword="xxx")
- 用户说"xxx第N集"→ get_episodes(series_keyword="xxx", season_number=N)
- 用户说"没看过的"→ get_play_status(status_filter="unplayed")
- 用户说"周杰伦的歌"→ search_songs_by_artist(artist_name="周杰伦")
- 用户说"下一集"→ get_next_up()
- 用户说"继续看"/"没看完的"→ get_resume_items()
- 用户说"最新"/"新到的"→ get_latest()
- 用户说"有哪些歌手"→ search_artists()
- 用户说"和xxx类似的"→ get_similar(keyword="xxx")
- 用户说"xxx的歌词"→ get_lyrics(keyword="xxx")
- 推荐类问题 → search_media, keyword 留空
- 只返回 JSON，不要其他文字"""

TOOL_MAP = {
    "search_media": search_media,
    "get_item_detail": get_item_detail,
    "get_item_overview": get_item_overview,
    "get_items_overview": get_items_overview,
    "get_episodes": get_episodes,
    "get_album_tracks": get_album_tracks,
    "get_play_status": get_play_status,
    "get_genres": get_genres,
    "get_years": get_years,
    "get_libraries": get_libraries,
    "get_media_stats": get_media_stats,
    "get_next_up": get_next_up,
    "get_resume_items": get_resume_items,
    "get_latest": get_latest,
    "search_artists": search_artists,
    "search_songs_by_artist": search_songs_by_artist,
    "get_similar": get_similar,
    "get_lyrics": get_lyrics,
}

# 每个 tool 的合法参数名
VALID_PARAMS = {
    "search_media": {"keyword", "media_type", "genres", "min_year", "max_year", "min_rating", "max_rating", "limit", "sort_by", "sort_order"},
    "get_item_detail": {"keyword", "item_id"},
    "get_item_overview": {"keyword", "item_id"},
    "get_items_overview": {"keyword", "media_type", "genres", "min_year", "max_year", "min_rating", "limit", "sort_by", "sort_order"},
    "get_episodes": {"series_keyword", "season_number"},
    "get_album_tracks": {"keyword"},
    "get_play_status": {"media_type", "keyword", "status_filter", "limit"},
    "get_genres": set(), "get_years": set(), "get_libraries": set(), "get_media_stats": set(),
    "get_next_up": {"limit"},
    "get_resume_items": {"media_type", "limit"},
    "get_latest": {"media_type", "limit"},
    "search_artists": {"keyword", "limit"},
    "search_songs_by_artist": {"artist_name", "limit"},
    "get_similar": {"keyword", "item_id", "limit"},
    "get_lyrics": {"keyword", "item_id"},
}

# 参数别名映射
ARG_ALIASES = {"query": "keyword", "title": "keyword", "name": "keyword", "type": "media_type",
               "start_year": "min_year", "end_year": "max_year", "season": "season_number",
               "filter": "status_filter", "status": "status_filter", "artist": "artist_name"}

MEDIA_TYPE_FIX = {"movie": "Movie", "series": "Series", "tv": "Series", "audio": "Audio",
                  "music": "Audio", "song": "Audio", "book": "Book"}
SORT_BY_OK = {"CommunityRating", "DateCreated", "SortName", "ProductionYear"}
SORT_ORDER_OK = {"Descending", "Ascending"}

_llm = None


# ── 卡片类型枚举 ──────────────────────────────────────────

class CardType(str, Enum):
    """卡片类型，帮助前端决定跳转到哪个详情页。"""
    VIDEO = "video"      # 电影、电视剧、剧集等
    MUSIC = "music"      # 歌曲、专辑、歌手等
    BOOK = "book"        # 书籍
    MANGA = "manga"      # 漫画


_JELLYFIN_TYPE_MAP: dict[str, CardType] = {
    "Movie": CardType.VIDEO,
    "Series": CardType.VIDEO,
    "Episode": CardType.VIDEO,
    "Video": CardType.VIDEO,
    "Season": CardType.VIDEO,
    "Audio": CardType.MUSIC,
    "MusicAlbum": CardType.MUSIC,
    "MusicArtist": CardType.MUSIC,
    "MusicVideo": CardType.MUSIC,
    "Book": CardType.BOOK,
    "ComicBook": CardType.MANGA,
}


def _card_type(jellyfin_type: str) -> str:
    """Jellyfin 类型 → 卡片类型枚举值，未知类型默认 video。"""
    return _JELLYFIN_TYPE_MAP.get(jellyfin_type, CardType.VIDEO).value


def get_llm():
    global _llm
    if _llm is None:
        _llm = ChatOpenAI(
            model="deepseek-chat",
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
        )
    return _llm


def clean_args(tool_name: str, raw: dict) -> dict:
    """清洗 LLM 返回的参数。"""
    valid = VALID_PARAMS.get(tool_name, set())
    # 别名映射 + 过滤 None/无效值
    out = {}
    for k, v in raw.items():
        rk = ARG_ALIASES.get(k, k)
        if rk not in valid:
            continue
        if v is None:
            continue
        # genres: list → str
        if rk == "genres" and isinstance(v, list):
            v = ",".join(str(g) for g in v)
        # 数值参数跳过 0
        if rk in ("min_year", "max_year", "min_rating", "max_rating") and v in (0, 0.0, "0", ""):
            continue
        # limit
        if rk == "limit":
            v = int(v) if isinstance(v, (int, float)) else v
            if v <= 0:
                continue
        # media_type 大小写
        if rk == "media_type" and isinstance(v, str):
            v = MEDIA_TYPE_FIX.get(v.lower(), v)
        if rk == "sort_by" and v not in SORT_BY_OK:
            v = "CommunityRating"
        if rk == "sort_order" and v not in SORT_ORDER_OK:
            v = "Descending"
        if rk == "status_filter" and v not in ("all", "unplayed", "played", "favorite"):
            v = "all"
        out[rk] = v
    return out


@app.post("/intent")
def intent(req: AskRequest):
    """LLM 意图分析 → 调工具 → 返回结果。空结果自动放宽条件重试。"""
    try:
        # 1. LLM 分析意图
        resp = get_llm().invoke([
            {"role": "system", "content": INTENT_PROMPT},
            {"role": "user", "content": req.question},
        ])
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        parsed = json.loads(raw)
        tool_name = parsed.get("tool", "")
        raw_args = parsed.get("args", {})
        tool_fn = TOOL_MAP.get(tool_name)
        if not tool_fn:
            return JSONResponse(status_code=400, content={"error": f"未知工具: {tool_name}", "parsed": parsed})

        # 2. 清洗参数
        args = clean_args(tool_name, raw_args)

        # 3. 调用工具
        result = tool_fn.invoke(args)

        # 4. 空结果 → 逐步放宽条件
        if "没有找到" in result:
            if args.get("keyword"):
                args = {k: v for k, v in args.items() if k != "keyword"}
                result = tool_fn.invoke(args)
            if "没有找到" in result and args.get("genres"):
                args = {k: v for k, v in args.items() if k != "genres"}
                result = tool_fn.invoke(args)

        return {"question": req.question, "intent": {"tool": tool_name, "args": args}, "result": result}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "traceback": traceback.format_exc()})


# ── 结构化推荐接口（给前端渲染用）──────────────────────────

RECOMMEND_PROMPT = """你是 Jellyfin 媒体库推荐助手。根据用户需求，返回搜索参数。

规则:
- 返回 JSON: {"media_type": "...", "genres": "...", "keyword": "...", "min_year": N, "max_year": N, "min_rating": N, "limit": N}
- 不需要的字段不要写
- media_type: 电影→Movie, 电视剧→Series, 歌曲→Audio, 书→Book
- genres 用英文: 科幻→Sci-Fi, 动作→Action, 喜剧→Comedy, 剧情→Drama, 恐怖→Horror, 动画→Anime, 爱情→Romance, 冒险→Adventure, 奇幻→Fantasy
- limit: 用户要几部就填几（如"推荐3部"→limit=3），没说数量默认 5
- 只返回 JSON"""


REASON_PROMPT = """根据用户的问题和搜索到的媒体列表，为**每个**媒体写一句简短的推荐理由（10-30字）。
必须为每个 index 都提供 reason，不能遗漏。

用户问题: {question}
媒体列表:
{items_json}

返回 JSON 数组，每个元素: {{"index": 0, "reason": "推荐理由"}}
只返回 JSON 数组，不要其他文字。"""


@app.post("/recommend")
def recommend(req: AskRequest):
    """自然语言 → 结构化推荐列表（前端可直接渲染）。

    返回格式:
    {
      "question": "...",
      "items": [
        {
          "id": "abc123",
          "name": "肖申克的救赎",
          "type": "Movie",
          "year": 1994,
          "rating": 8.7,
          "genres": ["剧情", "犯罪"],
          "overview": "...",
          "reason": "经典越狱励志片",
          "posterUrl": "http://localhost:8096/Items/abc123/Images/Primary",
          ...
        }
      ]
    }
    """
    try:
        question = req.question
        llm = get_llm()
        server_url = os.getenv("JELLYFIN_URL", "http://localhost:8096")

        # 1. LLM 提取搜索参数
        resp = llm.invoke([
            {"role": "system", "content": RECOMMEND_PROMPT},
            {"role": "user", "content": question},
        ])
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        search_params = json.loads(raw)

        # 2. 调用搜索，拿到结构化数据
        items = search_items_raw(
            keyword=search_params.get("keyword", ""),
            media_type=search_params.get("media_type", ""),
            genres=search_params.get("genres", ""),
            min_year=search_params.get("min_year"),
            max_year=search_params.get("max_year"),
            min_rating=search_params.get("min_rating"),
            limit=search_params.get("limit", 5),
        )

        # 空结果 → 去掉 genres 重试
        if not items and search_params.get("genres"):
            items = search_items_raw(
                keyword=search_params.get("keyword", ""),
                media_type=search_params.get("media_type", ""),
                limit=search_params.get("limit", 5),
            )

        # 空结果 → 去掉 keyword 重试
        if not items and search_params.get("keyword"):
            items = search_items_raw(
                media_type=search_params.get("media_type", ""),
                limit=search_params.get("limit", 5),
            )

        if not items:
            return {"question": question, "items": [], "total": 0}

        # 3. 拼装 posterUrl
        for item in items:
            item["posterUrl"] = f"{server_url}/Items/{item['id']}/Images/Primary" if item.get("id") else ""

        # 4. LLM 生成推荐理由
        items_brief = json.dumps([
            {"i": i, "name": it.get("name"), "year": it.get("year"),
             "rating": it.get("rating"), "genres": it.get("genres"),
             "overview": (it.get("overview") or "")[:100]}
            for i, it in enumerate(items)
        ], ensure_ascii=False)

        reason_resp = llm.invoke([
            {"role": "system", "content": REASON_PROMPT.format(question=question, items_json=items_brief)},
            {"role": "user", "content": question},
        ])
        reason_raw = reason_resp.content.strip()
        if reason_raw.startswith("```"):
            reason_raw = reason_raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            reasons = json.loads(reason_raw)
            reason_map = {r.get("index"): r.get("reason", "") for r in reasons}
        except (json.JSONDecodeError, TypeError):
            reason_map = {}

        for i, item in enumerate(items):
            item["reason"] = reason_map.get(i, "")
            item["cardType"] = _card_type(item.get("type", ""))

        return {"question": question, "items": items, "total": len(items)}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e), "traceback": traceback.format_exc()})


# ── 直接调工具接口（GET，不走 LLM）────────────────────────

@app.get("/search")
def api_search(
    keyword: str = "", media_type: str = "", genres: str = "",
    min_year: int = 0, max_year: int = 0, min_rating: float = 0, max_rating: float = 0,
    limit: int = 20, sort_by: str = "CommunityRating", sort_order: str = "Descending",
):
    server_url = os.getenv("JELLYFIN_URL", "http://localhost:8096")
    items = search_items_raw(
        keyword=keyword, media_type=media_type, genres=genres,
        min_year=min_year or None, max_year=max_year or None,
        min_rating=min_rating or None, max_rating=max_rating or None,
        limit=limit, sort_by=sort_by, sort_order=sort_order,
    )
    for item in items:
        item["posterUrl"] = f"{server_url}/Items/{item['id']}/Images/Primary" if item.get("id") else ""
    return {"items": items, "total": len(items)}


@app.get("/genres")
def api_genres():
    return {"result": get_genres.invoke({})}


@app.get("/years")
def api_years():
    return {"result": get_years.invoke({})}


@app.get("/libraries")
def api_libraries():
    return {"result": get_libraries.invoke({})}


@app.get("/stats")
def api_stats():
    return {"result": get_media_stats.invoke({})}


@app.get("/detail")
def api_detail(keyword: str = "", item_id: str = ""):
    return {"result": get_item_detail.invoke({"keyword": keyword, "item_id": item_id})}


@app.get("/overview")
def api_overview(keyword: str = "", item_id: str = ""):
    return {"result": get_item_overview.invoke({"keyword": keyword, "item_id": item_id})}


@app.get("/overviews")
def api_overviews(
    keyword: str = "", media_type: str = "", genres: str = "",
    min_year: int = 0, max_year: int = 0, min_rating: float = 0,
    limit: int = 10, sort_by: str = "CommunityRating", sort_order: str = "Descending",
):
    return {"result": get_items_overview.invoke({
        "keyword": keyword, "media_type": media_type, "genres": genres,
        "min_year": min_year or None, "max_year": max_year or None,
        "min_rating": min_rating or None, "limit": limit, "sort_by": sort_by, "sort_order": sort_order,
    })}


@app.get("/episodes")
def api_episodes(keyword: str, season: int = 1):
    return {"result": get_episodes.invoke({"series_keyword": keyword, "season_number": season})}


@app.get("/tracks")
def api_tracks(keyword: str):
    return {"result": get_album_tracks.invoke({"keyword": keyword})}


@app.get("/play_status")
def api_play_status(media_type: str = "", keyword: str = "", filter: str = "all", limit: int = 20):
    return {"result": get_play_status.invoke({
        "media_type": media_type, "keyword": keyword, "status_filter": filter, "limit": limit,
    })}


@app.get("/next_up")
def api_next_up(limit: int = 10):
    server_url = os.getenv("JELLYFIN_URL", "http://localhost:8096")
    items = get_next_up_raw(limit=limit)
    for item in items:
        item["posterUrl"] = f"{server_url}/Items/{item['id']}/Images/Primary" if item.get("id") else ""
    return {"items": items, "total": len(items)}


@app.get("/resume")
def api_resume(media_type: str = "Video", limit: int = 10):
    server_url = os.getenv("JELLYFIN_URL", "http://localhost:8096")
    items = get_resume_items_raw(media_type=media_type, limit=limit)
    for item in items:
        item["posterUrl"] = f"{server_url}/Items/{item['id']}/Images/Primary" if item.get("id") else ""
    return {"items": items, "total": len(items)}


@app.get("/latest")
def api_latest(media_type: str = "", limit: int = 10):
    server_url = os.getenv("JELLYFIN_URL", "http://localhost:8096")
    items = get_latest_raw(media_type=media_type, limit=limit)
    for item in items:
        item["posterUrl"] = f"{server_url}/Items/{item['id']}/Images/Primary" if item.get("id") else ""
    return {"items": items, "total": len(items)}


@app.get("/artists")
def api_artists(keyword: str = "", limit: int = 20):
    return {"items": search_artists_raw(keyword=keyword, limit=limit)}


@app.get("/songs_by_artist")
def api_songs_by_artist(artist: str, limit: int = 20):
    return {"result": search_songs_by_artist.invoke({"artist_name": artist, "limit": limit})}


@app.get("/similar")
def api_similar(keyword: str = "", item_id: str = "", limit: int = 10):
    return {"result": get_similar.invoke({"keyword": keyword, "item_id": item_id, "limit": limit})}


@app.get("/lyrics")
def api_lyrics(keyword: str = "", item_id: str = ""):
    return {"result": get_lyrics.invoke({"keyword": keyword, "item_id": item_id})}


# ── Debug / Benchmark ──────────────────────────────────────

@app.post("/debug/benchmark")
async def benchmark(req: BenchmarkRequest):
    """并发 benchmark 测试，直接返回 JSON 报告 + 保存 Markdown 表格。

    用法:
      POST /debug/benchmark
      {
        "questions": ["推荐3部科幻电影", "肖申克的救赎讲了什么", "今天天气怎么样"],
        "concurrency": 3,
        "repeat": 2
      }

    返回 JSON 报告，同时在 tests/ 目录生成 Markdown 表格文件。
    """
    concurrency = min(req.concurrency, 10)
    sem = asyncio.Semaphore(concurrency)

    # 展开任务列表
    task_questions: list[str] = []
    for q in req.questions:
        for _ in range(req.repeat):
            task_questions.append(q)
    total = len(task_questions)

    t0 = time.perf_counter()

    async def _run(idx: int, question: str):
        return idx, await run_single(question, sem)

    # 创建所有任务（semaphore 控制并发）
    tasks = [asyncio.create_task(_run(idx, q)) for idx, q in enumerate(task_questions)]
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)
    wall_time = round((time.perf_counter() - t0) * 1000, 1)

    # 收集结果
    traces: list[DebugTrace] = []
    for raw in results_raw:
        if isinstance(raw, Exception):
            # 创建错误 trace
            t = DebugTrace(enabled=True)
            t.error = str(raw)
            traces.append(t)
        else:
            idx, trace = raw
            trace.log()
            traces.append(trace)

    report = build_report(
        traces=traces,
        questions=list(dict.fromkeys(task_questions)),
        concurrency=concurrency,
        repeat=req.repeat,
        wall_time=wall_time,
    )

    # 保存 Markdown 报告
    try:
        report_file = save_report(report, run_id=req.run_id)
        report["report_file"] = report_file
    except Exception as e:
        report["report_file"] = ""
        report["save_error"] = str(e)

    return report
