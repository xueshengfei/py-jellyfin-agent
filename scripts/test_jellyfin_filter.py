"""测试 Jellyfin API - 筛选功能"""

import json
from jellyfin_apiclient_python import JellyfinClient
from jellyfin_apiclient_python.connection_manager import ConnectionManager
from jellyfin_apiclient_python.api import API


def create_client():
    server_url = "http://localhost:8096"
    username = "xue13"
    password = "123456"

    client = JellyfinClient()
    client.config.app("test-app", "0.0.1", "test-device", "device-id-001")
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
    user_id = user_info.get("Id", "")
    token = auth_result.get("AccessToken", "")
    client.config.data["auth.token"] = token
    client.config.data["auth.user_id"] = user_id
    client.config.data["auth.server"] = server_url

    return API(client.http), user_id


def print_items(items, total, label, max_show=10):
    """打印结果列表"""
    print(f"\n  {label}: 共 {total} 条")
    for i, item in enumerate(items[:max_show]):
        name = item.get("Name", "Unknown")
        year = item.get("ProductionYear", "")
        item_type = item.get("Type", "")
        rating = item.get("CommunityRating", "")
        genres = ", ".join(item.get("Genres", []))
        year_str = f"({year})" if year else ""
        rating_str = f" 评分:{rating}" if rating else ""
        genres_str = f" [{genres}]" if genres else ""
        print(f"    {i+1}. {name} {year_str}{rating_str}{genres_str}")
    if total > max_show:
        print(f"    ... 还有 {total - max_show} 条")


def filter_by_genre(api, user_id):
    """按风格筛选"""
    print("=" * 60)
    print("1. 按风格 (Genres) 筛选")
    print("=" * 60)

    test_genres = ["Action", "Comedy", "Anime", "Drama", "Sci-Fi"]
    for genre in test_genres:
        try:
            result = api._get("Items", params={
                "UserId": user_id,
                "Genres": genre,
                "Recursive": "true",
                "Limit": 10,
                "Fields": "ProductionYear,CommunityRating,Genres,Type",
                "SortBy": "CommunityRating",
                "SortOrder": "Descending",
            })
            total = result.get("TotalRecordCount", 0)
            items = result.get("Items", [])
            print_items(items, total, f"风格 '{genre}' 的所有内容")
        except Exception as e:
            print(f"  风格 '{genre}' 查询失败: {e}")


def filter_by_year(api, user_id):
    """按年份筛选"""
    print("\n" + "=" * 60)
    print("2. 按年份 (Years) 筛选")
    print("=" * 60)

    test_years = [2025, 2024, 2023]
    for year in test_years:
        try:
            result = api._get("Items", params={
                "UserId": user_id,
                "Years": str(year),
                "Recursive": "true",
                "Limit": 10,
                "Fields": "ProductionYear,CommunityRating,Genres,Type",
                "SortBy": "CommunityRating",
                "SortOrder": "Descending",
            })
            total = result.get("TotalRecordCount", 0)
            items = result.get("Items", [])
            print_items(items, total, f"{year} 年的内容")
        except Exception as e:
            print(f"  {year} 年查询失败: {e}")


def filter_by_type(api, user_id):
    """按类型筛选"""
    print("\n" + "=" * 60)
    print("3. 按媒体类型 (IncludeItemTypes) 筛选")
    print("=" * 60)

    types = [
        ("Movie", "电影 - 2024年"),
        ("Series", "电视剧 - 评分最高"),
        ("Book", "书籍"),
        ("Audio", "歌曲"),
    ]

    for item_type, label in types:
        params = {
            "UserId": user_id,
            "IncludeItemTypes": item_type,
            "Recursive": "true",
            "Limit": 10,
            "Fields": "ProductionYear,CommunityRating,Genres,AlbumArtist",
            "SortBy": "CommunityRating",
            "SortOrder": "Descending",
        }
        # 电影额外加年份筛选
        if "2024" in label:
            params["Years"] = "2024"

        try:
            result = api._get("Items", params=params)
            total = result.get("TotalRecordCount", 0)
            items = result.get("Items", [])
            extra = ""
            for i, item in enumerate(items[:10]):
                name = item.get("Name", "Unknown")
                year = item.get("ProductionYear", "")
                rating = item.get("CommunityRating", "")
                genres = ", ".join(item.get("Genres", []))
                artist = item.get("AlbumArtist", "")
                year_str = f"({year})" if year else ""
                rating_str = f" 评分:{rating}" if rating else ""
                genres_str = f" [{genres}]" if genres else ""
                artist_str = f" - {artist}" if artist else ""
                print(f"    {i+1}. {name}{artist_str} {year_str}{rating_str}{genres_str}")
            print(f"  [{label}] 共 {total} 条")
        except Exception as e:
            print(f"  {label} 查询失败: {e}")


def filter_combined(api, user_id):
    """组合筛选: 类型 + 风格 + 年份"""
    print("\n" + "=" * 60)
    print("4. 组合筛选 (类型 + 风格 + 年份)")
    print("=" * 60)

    combos = [
        {"label": "2025年 Action 电影", "params": {
            "IncludeItemTypes": "Movie", "Genres": "Action", "Years": "2025"
        }},
        {"label": "2024年 Comedy 电影", "params": {
            "IncludeItemTypes": "Movie", "Genres": "Comedy", "Years": "2024"
        }},
        {"label": "评分 > 8 的电影", "params": {
            "IncludeItemTypes": "Movie",
            "MinCommunityRating": "8",
            "SortBy": "CommunityRating", "SortOrder": "Descending",
        }},
        {"label": "2020-2025年的电视剧", "params": {
            "IncludeItemTypes": "Series",
            "MinYear": "2020", "MaxYear": "2025",
            "SortBy": "CommunityRating", "SortOrder": "Descending",
        }},
        {"label": "评分 > 9 的所有内容", "params": {
            "MinCommunityRating": "9",
            "SortBy": "CommunityRating", "SortOrder": "Descending",
        }},
        {"label": "未看过的电影 (IsPlayed=false)", "params": {
            "IncludeItemTypes": "Movie",
            "IsPlayed": "false",
            "SortBy": "DateCreated", "SortOrder": "Descending",
        }},
        {"label": "电影 - 按添加时间最新", "params": {
            "IncludeItemTypes": "Movie",
            "SortBy": "DateCreated", "SortOrder": "Descending",
        }},
    ]

    for combo in combos:
        label = combo["label"]
        extra_params = combo["params"]
        params = {
            "UserId": user_id,
            "Recursive": "true",
            "Limit": 10,
            "Fields": "ProductionYear,CommunityRating,Genres,Type,DateCreated",
        }
        params.update(extra_params)

        try:
            result = api._get("Items", params=params)
            total = result.get("TotalRecordCount", 0)
            items = result.get("Items", [])
            print_items(items, total, label)
        except Exception as e:
            print(f"  [{label}] 查询失败: {e}")


def filter_search(api, user_id):
    """搜索功能"""
    print("\n" + "=" * 60)
    print("5. 搜索功能")
    print("=" * 60)

    keywords = ["速度与激情", "Harry", "周杰伦"]
    for kw in keywords:
        try:
            result = api._get("Items", params={
                "UserId": user_id,
                "SearchTerm": kw,
                "Recursive": "true",
                "Limit": 10,
                "Fields": "ProductionYear,CommunityRating,Genres,Type",
            })
            total = result.get("TotalRecordCount", 0)
            items = result.get("Items", [])
            print_items(items, total, f"搜索 '{kw}'")
        except Exception as e:
            print(f"  搜索 '{kw}' 失败: {e}")


def filter_by_parent(api, user_id):
    """按媒体库筛选"""
    print("\n" + "=" * 60)
    print("6. 按媒体库 (ParentId) 筛选")
    print("=" * 60)

    # 先获取所有媒体库
    try:
        views = api.get_views()
        libraries = views.get("Items", [])
    except:
        libraries = []

    for lib in libraries[:7]:
        lib_name = lib.get("Name", "Unknown")
        lib_id = lib.get("Id", "")
        lib_type = lib.get("CollectionType", "")
        try:
            result = api._get("Items", params={
                "UserId": user_id,
                "ParentId": lib_id,
                "Recursive": "true",
                "Limit": 5,
                "Fields": "ProductionYear,CommunityRating,Genres,Type",
                "SortBy": "CommunityRating",
                "SortOrder": "Descending",
            })
            total = result.get("TotalRecordCount", 0)
            items = result.get("Items", [])
            print_items(items, total, f"媒体库 '{lib_name}' ({lib_type}) 评分最高", max_show=5)
        except Exception as e:
            print(f"  媒体库 '{lib_name}' 查询失败: {e}")


def main():
    api, user_id = create_client()
    print(f"已连接, 用户ID: {user_id}\n")

    filter_by_genre(api, user_id)
    filter_by_year(api, user_id)
    filter_by_type(api, user_id)
    filter_combined(api, user_id)
    filter_search(api, user_id)
    filter_by_parent(api, user_id)

    print("\n========== 所有筛选测试完成! ==========")


if __name__ == "__main__":
    main()
