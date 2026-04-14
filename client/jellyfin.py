"""Jellyfin API 封装 — LangChain Tool 调用层"""

import os
from typing import Optional
from langchain_core.tools import tool
from jellyfin_apiclient_python import JellyfinClient
from jellyfin_apiclient_python.connection_manager import ConnectionManager
from jellyfin_apiclient_python.api import API

# ── 全局单例 + 缓存 ────────────────────────────────────────
_api: API | None = None
_user_id: str = ""
_cache: dict = {}  # 预热缓存：genres, libraries, years, stats


def _connect() -> tuple[API, str]:
    """连接 Jellyfin 并返回 (API, user_id)，只执行一次。"""
    global _api, _user_id
    if _api is not None:
        return _api, _user_id
    return _do_connect()


def _do_connect() -> tuple[API, str]:
    """实际建立连接，内部使用。"""
    global _api, _user_id

    server_url = os.getenv("JELLYFIN_URL", "http://localhost:8096")
    username = os.getenv("JELLYFIN_USERNAME", "")
    password = os.getenv("JELLYFIN_PASSWORD", "")

    client = JellyfinClient()
    client.config.app("jellyfin-agent", "1.0.0", "agent-device", "agent-id-001")
    client.config.data.update({
        "auth.ssl": False,
        "auth.server": server_url,
        "auth.server-id": "",
        "auth.token": "",
        "auth.user_id": "",
    })

    cm = ConnectionManager(client)
    cm.connect_to_address(server_url)
    auth_result = cm.login(server_url, username, password)

    user_info = auth_result.get("User", {})
    _user_id = user_info.get("Id", "")
    token = auth_result.get("AccessToken", "")
    client.config.data["auth.token"] = token
    client.config.data["auth.user_id"] = _user_id
    client.config.data["auth.server"] = server_url

    _api = API(client.http)
    return _api, _user_id


def _reconnect():
    """强制重连（token 失效时调用）。"""
    global _api, _user_id
    _api = None
    _user_id = ""
    _do_connect()


def safe_get(endpoint: str, params: dict | None = None):
    """带 401 自动重连的 api._get 封装。"""
    api, user_id = _connect()
    try:
        return api._get(endpoint, params=params)
    except Exception as e:
        err_msg = str(e).lower()
        if "401" in err_msg or "unauthorized" in err_msg:
            print(f"[jellyfin] token 失效，自动重连: {e}")
            _reconnect()
            api, user_id = _connect()
            return api._get(endpoint, params=params)
        raise


def warm_cache():
    """预热缓存，连接后调用一次。"""
    api, user_id = _connect()

    # genres
    result = api._get("Genres", params={"UserId": user_id, "Limit": 200, "SortBy": "SortName", "SortOrder": "Ascending"})
    _cache["genres"] = [g.get("Name", "") for g in (result.get("Items", []) if isinstance(result, dict) else [])]

    # libraries
    views = api.get_views()
    _cache["libraries"] = [{"name": v.get("Name", ""), "type": v.get("CollectionType", "")} for v in views.get("Items", [])]

    # years
    result = api._get("Years", params={"UserId": user_id, "SortBy": "SortName", "SortOrder": "Descending", "Limit": 200})
    _cache["years"] = [y.get("Name", "") for y in (result.get("Items", []) if isinstance(result, dict) else [])]

    # stats
    type_labels = [("Movie", "电影"), ("Series", "电视剧"), ("Episode", "剧集"), ("Audio", "歌曲"), ("MusicAlbum", "音乐专辑"), ("Book", "书籍"), ("Video", "视频")]
    stats = {}
    for type_name, cn_name in type_labels:
        try:
            r = api._get("Items", params={"UserId": user_id, "IncludeItemTypes": type_name, "Recursive": "true", "Limit": 0})
            stats[cn_name] = r.get("TotalRecordCount", 0)
        except Exception:
            stats[cn_name] = -1
    _cache["stats"] = stats

    print(f"[cache] 预热完成: {len(_cache['genres'])} genres, {len(_cache['libraries'])} libraries, {len(_cache['years'])} years, {len(stats)} stats")


def refresh_cache():
    """强制刷新缓存（管理员手动触发）。"""
    global _api
    _api = None
    _cache.clear()
    warm_cache()


def _format_item(item: dict) -> str:
    """将单个媒体条目格式化为可读字符串。"""
    name = item.get("Name", "Unknown")
    year = item.get("ProductionYear", "")
    rating = item.get("CommunityRating", "")
    genres = ", ".join(item.get("Genres", []))
    item_type = item.get("Type", "")
    artist = item.get("AlbumArtist", "")

    parts = [name]
    if artist:
        parts.append(f"by {artist}")
    if year:
        parts.append(f"({year})")
    if rating:
        parts.append(f"评分:{rating}")
    if genres:
        parts.append(f"[{genres}]")
    if item_type:
        parts.append(f"类型:{item_type}")
    return " ".join(parts)


# ── 结构化数据函数（给前端 API 用）──────────────────────────

def _item_to_dict(item: dict) -> dict:
    """将 Jellyfin item 转为前端友好的结构化字典。"""
    runtime_ticks = item.get("RunTimeTicks", 0) or 0
    ud = item.get("UserData", {})
    pos_ticks = ud.get("PlaybackPositionTicks", 0)

    return {
        "id": item.get("Id", ""),
        "name": item.get("Name", ""),
        "originalTitle": item.get("OriginalTitle", ""),
        "type": item.get("Type", ""),
        "year": item.get("ProductionYear"),
        "rating": item.get("CommunityRating"),
        "officialRating": item.get("OfficialRating", ""),
        "genres": item.get("Genres", []),
        "overview": item.get("Overview", ""),
        "tagline": (item.get("Taglines", []) or [""])[0],
        "studios": [s.get("Name") for s in item.get("Studios", [])],
        "people": [
            {"name": p.get("Name"), "role": p.get("Role", ""), "type": p.get("Type", "")}
            for p in item.get("People", [])[:10]
        ],
        "runtimeMinutes": round(runtime_ticks / 600000000) if runtime_ticks else None,
        "status": item.get("Status", ""),
        "played": ud.get("Played", False),
        "playCount": ud.get("PlayCount", 0),
        "favorite": ud.get("IsFavorite", False),
        "positionMinutes": round(pos_ticks / 600000000) if pos_ticks else 0,
        "artist": item.get("AlbumArtist", ""),
        "artists": item.get("Artists", []),
        "providerIds": item.get("ProviderIds", {}),
        "premiereDate": item.get("PremiereDate", ""),
    }


def search_items_raw(
    keyword: str = "",
    media_type: str = "",
    genres: str = "",
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    min_rating: Optional[float] = None,
    max_rating: Optional[float] = None,
    limit: int = 20,
    sort_by: str = "CommunityRating",
    sort_order: str = "Descending",
) -> list[dict]:
    """搜索媒体，返回结构化列表（给前端渲染用）。"""
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)

    params = {
        "UserId": user_id,
        "Recursive": "true",
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,AlbumArtist,RunTimeTicks,Overview,OfficialRating,Studios,People,Taglines,ProviderIds,UserData,PremiereDate",
        "SortBy": sort_by,
        "SortOrder": sort_order,
    }
    if keyword:
        params["SearchTerm"] = keyword
    if media_type:
        params["IncludeItemTypes"] = media_type
    if genres:
        params["Genres"] = genres
    if min_year:
        params["MinYear"] = str(min_year)
    if max_year:
        params["MaxYear"] = str(max_year)
    if min_rating:
        params["MinCommunityRating"] = str(min_rating)
    if max_rating:
        params["MaxCommunityRating"] = str(max_rating)

    result = api._get("Items", params=params)
    items = result.get("Items", [])
    return [_item_to_dict(item) for item in items]


# ── 新增查询 _raw 辅助函数 ──────────────────────────────────

def get_next_up_raw(limit: int = 10) -> list[dict]:
    """获取"下一集"（追剧），返回结构化列表。"""
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)
    result = safe_get("Shows/NextUp", params={
        "UserId": user_id,
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,RunTimeTicks,Overview,SeriesPrimaryImage,SeriesName,UserData",
    })
    items = result.get("Items", [])
    return [_item_to_dict(item) for item in items]


def get_resume_items_raw(media_type: str = "", limit: int = 10) -> list[dict]:
    """获取继续播放的项目，返回结构化列表。"""
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)
    params = {
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,AlbumArtist,RunTimeTicks,Overview,OfficialRating,Studios,People,Taglines,ProviderIds,UserData,PremiereDate",
        "MediaTypes": media_type or "Video",
    }
    result = safe_get(f"Users/{user_id}/Items/Resume", params=params)
    items = result.get("Items", [])
    return [_item_to_dict(item) for item in items]


def get_latest_raw(media_type: str = "", limit: int = 10) -> list[dict]:
    """获取最新添加的媒体，返回结构化列表。"""
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)
    params = {
        "UserId": user_id,
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,AlbumArtist,RunTimeTicks,Overview,OfficialRating,Studios,People,Taglines,ProviderIds,UserData,PremiereDate",
    }
    if media_type:
        params["IncludeItemTypes"] = media_type
    result = safe_get("Items/Latest", params=params)
    # /Items/Latest 返回裸数组而非 {Items:[...]}
    if isinstance(result, list):
        items = result
    else:
        items = result.get("Items", [])
    return [_item_to_dict(item) for item in items]


def search_artists_raw(keyword: str = "", limit: int = 20) -> list[dict]:
    """搜索歌手/艺术家，返回结构化列表。"""
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)
    params = {
        "UserId": user_id,
        "Limit": limit,
        "SortBy": "SortName",
        "SortOrder": "Ascending",
    }
    if keyword:
        params["SearchTerm"] = keyword
    result = safe_get("Artists", params=params)
    items = result.get("Items", []) if isinstance(result, dict) else []
    return [
        {
            "id": item.get("Id", ""),
            "name": item.get("Name", ""),
            "type": "MusicArtist",
        }
        for item in items
    ]


# ── LangChain Tools ───────────────────────────────────────

@tool
def search_media(
    keyword: str = "",
    media_type: str = "",
    genres: str = "",
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    min_rating: Optional[float] = None,
    max_rating: Optional[float] = None,
    limit: int = 20,
    sort_by: str = "CommunityRating",
    sort_order: str = "Descending",
) -> str:
    """搜索 Jellyfin 媒体库。

    参数:
        keyword: 搜索关键字（标题模糊匹配）
        media_type: 媒体类型 — Movie / Series / Audio / Book / MusicAlbum，留空表示所有类型
        genres: 风格，如 "Action", "Sci-Fi", "Comedy"，多个用逗号分隔
        min_year: 最小年份（如 2020）
        max_year: 最大年份（如 2025）
        min_rating: 最低社区评分（如 8.0）
        max_rating: 最高社区评分
        limit: 返回条数上限（默认 20，最大 50）
        sort_by: 排序字段 — CommunityRating / DateCreated / SortName / ProductionYear
        sort_order: 排序方向 — Descending / Ascending
    """
    api, user_id = _connect()

    limit = min(max(limit, 1), 50)

    params = {
        "UserId": user_id,
        "Recursive": "true",
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,AlbumArtist,RunTimeTicks",
        "SortBy": sort_by,
        "SortOrder": sort_order,
    }

    if keyword:
        params["SearchTerm"] = keyword
    if media_type:
        params["IncludeItemTypes"] = media_type
    if genres:
        params["Genres"] = genres
    if min_year:
        params["MinYear"] = str(min_year)
    if max_year:
        params["MaxYear"] = str(max_year)
    if min_rating:
        params["MinCommunityRating"] = str(min_rating)
    if max_rating:
        params["MaxCommunityRating"] = str(max_rating)

    result = api._get("Items", params=params)
    total = result.get("TotalRecordCount", 0)
    items = result.get("Items", [])

    if not items:
        return "没有找到匹配的媒体。"

    lines = [f"共找到 {total} 条结果，展示前 {len(items)} 条:"]
    for i, item in enumerate(items, 1):
        lines.append(f"  {i}. {_format_item(item)}")
    if total > limit:
        lines.append(f"  ... 还有 {total - limit} 条未展示")
    return "\n".join(lines)


@tool
def get_genres() -> str:
    """获取 Jellyfin 媒体库中所有可用的风格（Genres）列表。"""
    if _cache.get("genres"):
        names = _cache["genres"]
        return f"共有 {len(names)} 个风格: " + ", ".join(names)

    api, user_id = _connect()
    result = api._get("Genres", params={
        "UserId": user_id,
        "Limit": 100,
        "SortBy": "SortName",
        "SortOrder": "Ascending",
    })

    items = result.get("Items", []) if isinstance(result, dict) else []
    if not items:
        return "未获取到风格列表。"

    names = [g.get("Name", "Unknown") for g in items]
    return f"共有 {len(names)} 个风格: " + ", ".join(names)


@tool
def get_years() -> str:
    """获取 Jellyfin 媒体库中所有可用的年份列表。"""
    if _cache.get("years"):
        years = _cache["years"]
        return f"共有 {len(years)} 个年份: " + ", ".join(years)

    api, user_id = _connect()
    result = api._get("Years", params={
        "UserId": user_id,
        "SortBy": "SortName",
        "SortOrder": "Descending",
        "Limit": 100,
    })

    items = result.get("Items", []) if isinstance(result, dict) else []
    if not items:
        return "未获取到年份列表。"

    years = [y.get("Name", "Unknown") for y in items]
    return f"共有 {len(years)} 个年份: " + ", ".join(years)


@tool
def get_libraries() -> str:
    """获取 Jellyfin 媒体库列表（如电影库、电视剧库、音乐库等）。"""
    if _cache.get("libraries"):
        libs = _cache["libraries"]
        lines = [f"共有 {len(libs)} 个媒体库:"]
        for lib in libs:
            lines.append(f"  - {lib['name']} (类型: {lib['type'] or 'N/A'})")
        return "\n".join(lines)

    api, user_id = _connect()
    views = api.get_views()
    items = views.get("Items", [])
    if not items:
        return "未找到媒体库。"

    lines = [f"共有 {len(items)} 个媒体库:"]
    for item in items:
        name = item.get("Name", "Unknown")
        ctype = item.get("CollectionType", "N/A")
        lines.append(f"  - {name} (类型: {ctype})")
    return "\n".join(lines)


@tool
def get_media_stats() -> str:
    """获取 Jellyfin 媒体库中各类型媒体的数量统计。"""
    if _cache.get("stats"):
        stats = _cache["stats"]
        lines = ["媒体库统计:"]
        for cn_name, count in stats.items():
            lines.append(f"  {cn_name}: {count if count >= 0 else '查询失败'}")
        return "\n".join(lines)

    api, user_id = _connect()
    type_labels = [
        ("Movie", "电影"),
        ("Series", "电视剧"),
        ("Episode", "剧集"),
        ("Audio", "歌曲"),
        ("MusicAlbum", "音乐专辑"),
        ("Book", "书籍"),
        ("Video", "视频"),
    ]

    lines = ["媒体库统计:"]
    for type_name, cn_name in type_labels:
        try:
            result = api._get("Items", params={
                "UserId": user_id,
                "IncludeItemTypes": type_name,
                "Recursive": "true",
                "Limit": 0,
            })
            total = result.get("TotalRecordCount", 0)
            lines.append(f"  {cn_name}: {total}")
        except Exception as e:
            lines.append(f"  {cn_name}: 查询失败 - {e}")
    return "\n".join(lines)


# ── 详情 / 元数据 Tools ─────────────────────────────────────

@tool
def get_item_detail(keyword: str = "", item_id: str = "") -> str:
    """获取某个媒体条目的完整元数据（简介、演职员、工作室、评分等）。

    通过关键字搜索或直接传入 item_id 获取详情。

    参数:
        keyword: 搜索关键字（先搜索再取第一条的详情）
        item_id: 直接指定 Jellyfin Item ID（优先级高于 keyword）
    """
    api, user_id = _connect()

    if not item_id and not keyword:
        return "请提供 keyword 或 item_id 参数。"

    if not item_id:
        result = api._get("Items", params={
            "UserId": user_id,
            "SearchTerm": keyword,
            "Recursive": "true",
            "Limit": 1,
        })
        items = result.get("Items", [])
        if not items:
            return f"未找到 '{keyword}' 相关的媒体。"
        item_id = items[0]["Id"]

    detail = api._get(f"Users/{user_id}/Items/{item_id}")

    name = detail.get("Name", "Unknown")
    item_type = detail.get("Type", "")
    year = detail.get("ProductionYear", "")
    rating = detail.get("CommunityRating", "")
    overview = detail.get("Overview", "暂无简介")
    official_rating = detail.get("OfficialRating", "")
    genres = ", ".join(detail.get("Genres", []))
    studios = ", ".join(s["Name"] for s in detail.get("Studios", []))
    taglines = " / ".join(detail.get("Taglines", []))
    runtime_ticks = detail.get("RunTimeTicks", 0) or 0
    runtime_min = round(runtime_ticks / 600000000) if runtime_ticks else 0
    status = detail.get("Status", "")

    people = detail.get("People", [])
    actors = [f'{p["Name"]}({p.get("Role", "")})' for p in people if p.get("Type") == "Actor"][:10]
    directors = [p["Name"] for p in people if p.get("Type") == "Director"]

    ud = detail.get("UserData", {})
    played = ud.get("Played", False)
    play_count = ud.get("PlayCount", 0)
    fav = ud.get("IsFavorite", False)
    pos_ticks = ud.get("PlaybackPositionTicks", 0)
    pos_min = round(pos_ticks / 600000000) if pos_ticks else 0

    lines = [f"=== {name} ({item_type}) ==="]
    if year:
        lines.append(f"年份: {year}")
    if rating:
        lines.append(f"评分: {rating}")
    if official_rating:
        lines.append(f"分级: {official_rating}")
    if genres:
        lines.append(f"风格: {genres}")
    if studios:
        lines.append(f"工作室: {studios}")
    if taglines:
        lines.append(f"标语: {taglines}")
    if runtime_min:
        lines.append(f"时长: {runtime_min} 分钟")
    if status:
        lines.append(f"状态: {status}")
    if directors:
        lines.append(f"导演: {', '.join(directors)}")
    if actors:
        lines.append(f"演员: {', '.join(actors)}")

    play_status = "已看完" if played else f"看到 {pos_min} 分钟" if pos_min else "未观看"
    fav_str = " [收藏]" if fav else ""
    lines.append(f"播放状态: {play_status} (播放{play_count}次){fav_str}")

    lines.append(f"\n简介:\n{overview}")
    return "\n".join(lines)


@tool
def get_item_overview(keyword: str = "", item_id: str = "") -> str:
    """获取某个媒体条目的简介（Overview）。

    参数:
        keyword: 搜索关键字
        item_id: 直接指定 Jellyfin Item ID
    """
    api, user_id = _connect()

    if not item_id and not keyword:
        return "请提供 keyword 或 item_id 参数。"

    if not item_id:
        result = api._get("Items", params={
            "UserId": user_id,
            "SearchTerm": keyword,
            "Recursive": "true",
            "Limit": 1,
            "Fields": "Overview",
        })
        items = result.get("Items", [])
        if not items:
            return f"未找到 '{keyword}' 相关的媒体。"
        item_id = items[0]["Id"]

    detail = api._get(f"Users/{user_id}/Items/{item_id}")

    name = detail.get("Name", "Unknown")
    overview = detail.get("Overview", "暂无简介")
    return f"{name}:\n{overview}"


@tool
def get_items_overview(
    keyword: str = "",
    media_type: str = "",
    genres: str = "",
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    min_rating: Optional[float] = None,
    limit: int = 10,
    sort_by: str = "CommunityRating",
    sort_order: str = "Descending",
) -> str:
    """批量获取多个媒体条目的简介。先搜索符合条件的条目，然后逐个获取简介。

    参数:
        keyword: 搜索关键字
        media_type: 媒体类型 — Movie / Series / Audio / Book
        genres: 风格筛选
        min_year / max_year: 年份范围
        min_rating: 最低评分
        limit: 最多获取几条简介（默认 10，最大 20）
        sort_by / sort_order: 排序方式
    """
    api, user_id = _connect()

    limit = min(max(limit, 1), 20)

    params = {
        "UserId": user_id,
        "Recursive": "true",
        "Limit": limit,
        "Fields": "Overview",
        "SortBy": sort_by,
        "SortOrder": sort_order,
    }
    if keyword:
        params["SearchTerm"] = keyword
    if media_type:
        params["IncludeItemTypes"] = media_type
    if genres:
        params["Genres"] = genres
    if min_year:
        params["MinYear"] = str(min_year)
    if max_year:
        params["MaxYear"] = str(max_year)
    if min_rating:
        params["MinCommunityRating"] = str(min_rating)

    result = api._get("Items", params=params)
    items = result.get("Items", [])
    total = result.get("TotalRecordCount", 0)

    if not items:
        return "没有找到匹配的媒体。"

    lines = [f"共 {total} 条结果，展示 {len(items)} 条简介:\n"]
    for i, item in enumerate(items, 1):
        name = item.get("Name", "Unknown")
        year = item.get("ProductionYear", "")
        overview = item.get("Overview", "暂无简介")
        year_str = f" ({year})" if year else ""
        lines.append(f"--- {i}. {name}{year_str} ---")
        lines.append(f"{overview}\n")
    return "\n".join(lines)


@tool
def get_episodes(series_keyword: str, season_number: int = 1) -> str:
    """获取某部电视剧指定季的剧集列表（含每集简介）。

    参数:
        series_keyword: 电视剧名称关键字
        season_number: 季号（默认第 1 季）
    """
    api, user_id = _connect()

    # 先搜 Series
    result = api._get("Items", params={
        "UserId": user_id,
        "SearchTerm": series_keyword,
        "IncludeItemTypes": "Series",
        "Recursive": "true",
        "Limit": 1,
    })
    items = result.get("Items", [])
    if not items:
        return f"未找到电视剧 '{series_keyword}'。"

    series = items[0]
    series_id = series["Id"]
    series_name = series.get("Name", "")

    # 获取该季的剧集
    episodes = api._get(f"Shows/{series_id}/Episodes", params={
        "UserId": user_id,
        "Season": season_number,
        "Fields": "Overview,CommunityRating,RunTimeTicks",
    })
    ep_items = episodes.get("Items", [])

    if not ep_items:
        return f"'{series_name}' 第 {season_number} 季没有找到剧集。"

    lines = [f"=== {series_name} 第 {season_number} 季 ({len(ep_items)} 集) ===\n"]
    for ep in ep_items:
        ep_num = ep.get("IndexNumber", "?")
        name = ep.get("Name", "Unknown")
        rating = ep.get("CommunityRating", "")
        runtime_ticks = ep.get("RunTimeTicks", 0) or 0
        minutes = round(runtime_ticks / 600000000) if runtime_ticks else 0
        overview = ep.get("Overview", "暂无简介")

        rating_str = f" 评分:{rating}" if rating else ""
        runtime_str = f" ({minutes}min)" if minutes else ""
        lines.append(f"  第{ep_num}集: {name}{runtime_str}{rating_str}")
        if overview:
            lines.append(f"    {overview[:120]}")
        lines.append("")

    return "\n".join(lines)


@tool
def get_album_tracks(keyword: str) -> str:
    """获取某个音乐专辑的歌曲列表。

    参数:
        keyword: 专辑名称或歌手名关键字
    """
    api, user_id = _connect()

    # 先搜 MusicAlbum
    result = api._get("Items", params={
        "UserId": user_id,
        "SearchTerm": keyword,
        "IncludeItemTypes": "MusicAlbum",
        "Recursive": "true",
        "Limit": 3,
        "Fields": "AlbumArtist,ProductionYear,Overview",
    })
    albums = result.get("Items", [])
    if not albums:
        return f"未找到与 '{keyword}' 相关的音乐专辑。"

    lines = []
    for album in albums:
        album_id = album["Id"]
        album_name = album.get("Name", "")
        artist = album.get("AlbumArtist", "")
        year = album.get("ProductionYear", "")
        year_str = f" ({year})" if year else ""

        lines.append(f"=== {album_name} by {artist}{year_str} ===")

        songs = api._get("Items", params={
            "UserId": user_id,
            "ParentId": album_id,
            "Recursive": "true",
            "Fields": "RunTimeTicks,Artists",
            "SortBy": "SortName",
            "SortOrder": "Ascending",
        })
        track_items = songs.get("Items", [])
        for j, track in enumerate(track_items, 1):
            tname = track.get("Name", "Unknown")
            artists = ", ".join(track.get("Artists", []))
            runtime_ticks = track.get("RunTimeTicks", 0) or 0
            seconds = round(runtime_ticks / 10000000) if runtime_ticks else 0
            m, s = divmod(seconds, 60)
            dur = f"{m}:{s:02d}" if seconds else ""
            artist_str = f" - {artists}" if artists else ""
            dur_str = f" [{dur}]" if dur else ""
            lines.append(f"  {j}. {tname}{artist_str}{dur_str}")

        lines.append(f"  共 {len(track_items)} 首\n")

    return "\n".join(lines)


@tool
def get_play_status(
    media_type: str = "",
    keyword: str = "",
    status_filter: str = "all",
    limit: int = 20,
) -> str:
    """获取媒体条目的播放状态（已看/未看/播放进度/收藏）。

    参数:
        media_type: 媒体类型 — Movie / Series / Audio / Book，留空表示所有
        keyword: 搜索关键字
        status_filter: 播放状态筛选 — "all"(全部) / "unplayed"(未看) / "played"(已看) / "favorite"(收藏)
        limit: 返回条数（默认 20）
    """
    api, user_id = _connect()

    limit = min(max(limit, 1), 50)

    params = {
        "UserId": user_id,
        "Recursive": "true",
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,RunTimeTicks",
        "SortBy": "DateCreated",
        "SortOrder": "Descending",
    }
    if media_type:
        params["IncludeItemTypes"] = media_type
    if keyword:
        params["SearchTerm"] = keyword

    if status_filter == "unplayed":
        params["IsPlayed"] = "false"
    elif status_filter == "played":
        params["IsPlayed"] = "true"
    elif status_filter == "favorite":
        params["IsFavorite"] = "true"

    result = api._get("Items", params=params)
    items = result.get("Items", [])
    total = result.get("TotalRecordCount", 0)

    if not items:
        return "没有找到匹配的媒体。"

    lines = [f"共 {total} 条，展示 {len(items)} 条:\n"]
    for i, item in enumerate(items, 1):
        name = item.get("Name", "Unknown")
        year = item.get("ProductionYear", "")
        item_type = item.get("Type", "")
        runtime_ticks = item.get("RunTimeTicks", 0) or 0
        total_min = round(runtime_ticks / 600000000) if runtime_ticks else 0

        ud = item.get("UserData", {})
        played = ud.get("Played", False)
        play_count = ud.get("PlayCount", 0)
        fav = ud.get("IsFavorite", False)
        pos_ticks = ud.get("PlaybackPositionTicks", 0)
        pos_min = round(pos_ticks / 600000000) if pos_ticks else 0

        year_str = f" ({year})" if year else ""
        if played:
            status = "已看完"
        elif pos_min:
            status = f"看到 {pos_min}/{total_min}min"
        else:
            status = "未观看"
        fav_str = " [收藏]" if fav else ""
        type_str = f" [{item_type}]" if item_type else ""

        lines.append(f"  {i}. {name}{year_str}{type_str}: {status} (播放{play_count}次){fav_str}")
    return "\n".join(lines)


@tool
def search_media_json(
    keyword: str = "",
    media_type: str = "",
    genres: str = "",
    min_year: Optional[int] = None,
    max_year: Optional[int] = None,
    min_rating: Optional[float] = None,
    max_rating: Optional[float] = None,
    limit: int = 20,
    sort_by: str = "CommunityRating",
    sort_order: str = "Descending",
) -> str:
    """搜索媒体库并返回 JSON 数组（用于前端卡片渲染）。

    参数与 search_media 完全一致，但返回 JSON 格式的结构化数据。
    """
    import json as _json
    items = search_items_raw(
        keyword=keyword, media_type=media_type, genres=genres,
        min_year=min_year, max_year=max_year,
        min_rating=min_rating, max_rating=max_rating,
        limit=limit, sort_by=sort_by, sort_order=sort_order,
    )
    return _json.dumps(items, ensure_ascii=False)


# ── 新增查询 Tools ───────────────────────────────────────

@tool
def get_next_up(limit: int = 10) -> str:
    """获取"下一集"列表 — 追剧时使用，展示用户正在追的剧的下一集。

    参数:
        limit: 返回条数上限（默认 10，最大 50）
    """
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)
    result = safe_get("Shows/NextUp", params={
        "UserId": user_id,
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,RunTimeTicks,Overview,SeriesPrimaryImage,SeriesName,UserData",
    })
    items = result.get("Items", [])
    if not items:
        return "没有正在追的剧。"

    lines = [f"共有 {len(items)} 个下一集:\n"]
    for i, item in enumerate(items, 1):
        name = item.get("Name", "Unknown")
        series_name = item.get("SeriesName", "")
        ep_num = item.get("IndexNumber", "?")
        season_num = item.get("ParentIndexNumber", "?")
        runtime_ticks = item.get("RunTimeTicks", 0) or 0
        minutes = round(runtime_ticks / 600000000) if runtime_ticks else 0
        series_str = f"[{series_name}] " if series_name else ""
        ep_str = f"S{season_num:02d}E{ep_num:02d}" if isinstance(ep_num, int) and isinstance(season_num, int) else ""
        runtime_str = f" ({minutes}min)" if minutes else ""
        lines.append(f"  {i}. {series_str}{ep_str} {name}{runtime_str}")

    return "\n".join(lines)


@tool
def get_resume_items(media_type: str = "Video", limit: int = 10) -> str:
    """获取"继续播放"列表 — 用户没看完的媒体。

    参数:
        media_type: 媒体类型 — Video(视频,默认) / Audio(音频)
        limit: 返回条数上限（默认 10，最大 50）
    """
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)
    params = {
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,RunTimeTicks,Overview,UserData",
        "MediaTypes": media_type or "Video",
    }
    result = safe_get(f"Users/{user_id}/Items/Resume", params=params)
    items = result.get("Items", [])
    if not items:
        return "没有未看完的媒体。"

    lines = [f"共 {len(items)} 个未看完的媒体:\n"]
    for i, item in enumerate(items, 1):
        name = item.get("Name", "Unknown")
        item_type = item.get("Type", "")
        runtime_ticks = item.get("RunTimeTicks", 0) or 0
        total_min = round(runtime_ticks / 600000000) if runtime_ticks else 0
        ud = item.get("UserData", {})
        pos_ticks = ud.get("PlaybackPositionTicks", 0)
        pos_min = round(pos_ticks / 600000000) if pos_ticks else 0
        type_str = f" [{item_type}]" if item_type else ""
        pos_str = f" 看到 {pos_min}/{total_min}min" if pos_min else ""
        lines.append(f"  {i}. {name}{type_str}{pos_str}")

    return "\n".join(lines)


@tool
def get_latest(media_type: str = "", limit: int = 10) -> str:
    """获取最近添加到媒体库的内容。

    参数:
        media_type: 媒体类型 — Movie / Series / Audio / Book / MusicAlbum，留空表示所有
        limit: 返回条数上限（默认 10，最大 50）
    """
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)
    params = {
        "UserId": user_id,
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,AlbumArtist,RunTimeTicks",
    }
    if media_type:
        params["IncludeItemTypes"] = media_type
    result = safe_get("Items/Latest", params=params)
    # /Items/Latest 返回裸数组而非 {Items:[...]}
    if isinstance(result, list):
        items = result
    else:
        items = result.get("Items", [])

    if not items:
        return "没有最近添加的内容。"

    lines = [f"最近添加的 {len(items)} 个内容:\n"]
    for i, item in enumerate(items, 1):
        lines.append(f"  {i}. {_format_item(item)}")

    return "\n".join(lines)


@tool
def search_artists(keyword: str = "", limit: int = 20) -> str:
    """搜索媒体库中的歌手/艺术家。

    参数:
        keyword: 搜索关键字（歌手名模糊匹配）
        limit: 返回条数上限（默认 20，最大 50）
    """
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)
    params = {
        "UserId": user_id,
        "Limit": limit,
        "SortBy": "SortName",
        "SortOrder": "Ascending",
    }
    if keyword:
        params["SearchTerm"] = keyword
    result = safe_get("Artists", params=params)
    items = result.get("Items", []) if isinstance(result, dict) else []
    if not items:
        return "没有找到匹配的歌手。"

    total = result.get("TotalRecordCount", len(items)) if isinstance(result, dict) else len(items)
    lines = [f"共找到 {total} 个歌手，展示 {len(items)} 个:\n"]
    for i, item in enumerate(items, 1):
        name = item.get("Name", "Unknown")
        lines.append(f"  {i}. {name}")

    return "\n".join(lines)


@tool
def search_songs_by_artist(artist_name: str, limit: int = 20) -> str:
    """根据歌手名搜索该歌手的所有歌曲。

    先搜索歌手获取 Artist ID，再按 AlbumArtistIds 查询所有 Audio 条目。
    注意: Artists ID ≠ Items ID，AlbumArtistIds 参数必须用 Artist ID。

    参数:
        artist_name: 歌手名称
        limit: 返回条数上限（默认 20，最大 50）
    """
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)

    # 1. 搜索 Artist 拿 ID
    artist_result = safe_get("Artists", params={
        "UserId": user_id,
        "SearchTerm": artist_name,
        "Limit": 1,
    })
    artists = artist_result.get("Items", []) if isinstance(artist_result, dict) else []
    if not artists:
        return f"未找到歌手 '{artist_name}'。"

    artist = artists[0]
    artist_id = artist["Id"]
    artist_display_name = artist.get("Name", artist_name)

    # 2. 按 AlbumArtistIds 查询歌曲（AlbumArtistIds 用的是 Artist ID）
    songs = safe_get("Items", params={
        "UserId": user_id,
        "AlbumArtistIds": artist_id,
        "IncludeItemTypes": "Audio",
        "Recursive": "true",
        "Limit": limit,
        "Fields": "AlbumArtist,Artists,RunTimeTicks,Album,ProductionYear",
        "SortBy": "SortName",
        "SortOrder": "Ascending",
    })
    items = songs.get("Items", [])
    total = songs.get("TotalRecordCount", 0)

    if not items:
        return f"歌手 '{artist_display_name}' 没有找到歌曲。"

    lines = [f"歌手 '{artist_display_name}' 的歌曲（共 {total} 首，展示 {len(items)} 首）:\n"]
    for i, item in enumerate(items, 1):
        name = item.get("Name", "Unknown")
        album = item.get("Album", "")
        runtime_ticks = item.get("RunTimeTicks", 0) or 0
        seconds = round(runtime_ticks / 10000000) if runtime_ticks else 0
        m, s = divmod(seconds, 60)
        dur = f"{m}:{s:02d}" if seconds else ""
        album_str = f" [{album}]" if album else ""
        dur_str = f" ({dur})" if dur else ""
        lines.append(f"  {i}. {name}{album_str}{dur_str}")

    if total > limit:
        lines.append(f"  ... 还有 {total - limit} 首未展示")

    return "\n".join(lines)


@tool
def search_songs_by_artist_json(artist_name: str, limit: int = 20) -> str:
    """根据歌手名搜索该歌手的所有歌曲，返回 JSON 数组（用于前端卡片渲染）。

    参数与 search_songs_by_artist 完全一致，但返回 JSON 格式的结构化数据。
    """
    import json as _json
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)

    artist_result = safe_get("Artists", params={
        "UserId": user_id,
        "SearchTerm": artist_name,
        "Limit": 1,
    })
    artists = artist_result.get("Items", []) if isinstance(artist_result, dict) else []
    if not artists:
        return _json.dumps([], ensure_ascii=False)

    artist_id = artists[0]["Id"]

    songs = safe_get("Items", params={
        "UserId": user_id,
        "AlbumArtistIds": artist_id,
        "IncludeItemTypes": "Audio",
        "Recursive": "true",
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,AlbumArtist,Artists,RunTimeTicks,Overview,OfficialRating,Studios,People,Taglines,ProviderIds,UserData,PremiereDate",
        "SortBy": "SortName",
        "SortOrder": "Ascending",
    })
    items = songs.get("Items", [])
    return _json.dumps([_item_to_dict(item) for item in items], ensure_ascii=False)


@tool
def get_similar(keyword: str = "", item_id: str = "", limit: int = 10) -> str:
    """获取与某个媒体条目相似的内容。

    参数:
        keyword: 搜索关键字（先搜索再取第一条的相似内容）
        item_id: 直接指定 Jellyfin Item ID（优先级高于 keyword）
        limit: 返回条数上限（默认 10，最大 50）
    """
    api, user_id = _connect()
    limit = min(max(limit, 1), 50)

    if not item_id and not keyword:
        return "请提供 keyword 或 item_id 参数。"

    if not item_id:
        result = safe_get("Items", params={
            "UserId": user_id,
            "SearchTerm": keyword,
            "Recursive": "true",
            "Limit": 1,
        })
        items = result.get("Items", [])
        if not items:
            return f"未找到 '{keyword}' 相关的媒体。"
        item_id = items[0]["Id"]

    similar = safe_get(f"Items/{item_id}/Similar", params={
        "UserId": user_id,
        "Limit": limit,
        "Fields": "ProductionYear,CommunityRating,Genres,Type,AlbumArtist,RunTimeTicks",
    })
    items = similar.get("Items", []) if isinstance(similar, dict) else []
    if not items:
        return "没有找到相似的内容。"

    lines = [f"共 {len(items)} 个相似内容:\n"]
    for i, item in enumerate(items, 1):
        lines.append(f"  {i}. {_format_item(item)}")

    return "\n".join(lines)


@tool
def get_lyrics(keyword: str = "", item_id: str = "") -> str:
    """获取歌曲的歌词。

    先搜索歌曲获取 ID，再通过 Audio/{id}/Lyrics 获取歌词。
    注意: 依赖歌词插件，未安装时可能报错。

    参数:
        keyword: 搜索关键字（歌曲名）
        item_id: 直接指定 Jellyfin Audio Item ID
    """
    api, user_id = _connect()

    if not item_id and not keyword:
        return "请提供 keyword 或 item_id 参数。"

    if not item_id:
        result = safe_get("Items", params={
            "UserId": user_id,
            "SearchTerm": keyword,
            "IncludeItemTypes": "Audio",
            "Recursive": "true",
            "Limit": 1,
        })
        items = result.get("Items", [])
        if not items:
            return f"未找到歌曲 '{keyword}'。"
        item_id = items[0]["Id"]

    try:
        lyrics_data = safe_get(f"Audio/{item_id}/Lyrics")
    except Exception as e:
        return f"获取歌词失败（可能未安装歌词插件）: {e}"

    if not lyrics_data:
        return "该歌曲暂无歌词。"

    # 解析歌词
    lyrics_content = lyrics_data.get("Lyrics", [])
    if isinstance(lyrics_content, list):
        lines_ = []
        for line in lyrics_content:
            text = line.get("Text", "") if isinstance(line, dict) else str(line)
            if text:
                lines_.append(text)
        if lines_:
            return "\n".join(lines_)

    if isinstance(lyrics_data, str):
        return lyrics_data

    if isinstance(lyrics_data, dict):
        for key in ("Lyrics", "Text", "Content"):
            val = lyrics_data.get(key, "")
            if val:
                if isinstance(val, str):
                    return val
                if isinstance(val, list):
                    texts = [l.get("Text", "") if isinstance(l, dict) else str(l) for l in val]
                    return "\n".join(t for t in texts if t)

    return "该歌曲暂无歌词。"


# 导出所有工具，供 agent.py 使用
ALL_TOOLS = [
    search_media, search_media_json,
    get_genres, get_years, get_libraries, get_media_stats,
    get_item_detail, get_item_overview, get_items_overview,
    get_episodes, get_album_tracks, get_play_status,
    get_next_up, get_resume_items, get_latest, search_artists,
    search_songs_by_artist, search_songs_by_artist_json, get_similar, get_lyrics,
]
