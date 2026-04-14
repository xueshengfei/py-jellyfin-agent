"""测试 Jellyfin API - 获取标签、媒体数量、歌手等信息"""

from jellyfin_apiclient_python import JellyfinClient
from jellyfin_apiclient_python.connection_manager import ConnectionManager
from jellyfin_apiclient_python.api import API


def create_client():
    server_url = "http://localhost:8096"
    username = "xue13"
    password = "123456"

    client = JellyfinClient()
    client.config.app("test-app", "0.0.1", "test-device", "device-id-001")
    client.config.data["auth.ssl"] = False
    client.config.data["auth.server"] = server_url
    client.config.data["auth.server-id"] = ""
    client.config.data["auth.token"] = ""
    client.config.data["auth.user_id"] = ""

    cm = ConnectionManager(client)
    cm.connect_to_address(server_url)
    auth_result = cm.login(server_url, username, password)
    user_info = auth_result.get("User", {})
    user_id = user_info.get("Id", "")
    token = auth_result.get("AccessToken", "")
    client.config.data["auth.token"] = token
    client.config.data["auth.user_id"] = user_id
    client.config.data["auth.server"] = server_url

    api = API(client.http)
    return api, user_id


def test_item_counts(api, user_id):
    """获取各种类型媒体的数量"""
    print("=" * 60)
    print("1. 各类型媒体数量")
    print("=" * 60)

    media_types = [
        ("Movie", "电影"),
        ("Series", "电视剧/系列"),
        ("Episode", "剧集"),
        ("Audio", "音频/歌曲"),
        ("MusicAlbum", "音乐专辑"),
        ("Book", "书籍"),
        ("Season", "季"),
        ("Video", "视频"),
        ("Photo", "照片"),
    ]

    for type_name, cn_name in media_types:
        try:
            result = api._get("Items", params={
                "UserId": user_id,
                "IncludeItemTypes": type_name,
                "Recursive": "true",
                "Limit": 0,
            })
            total = result.get("TotalRecordCount", 0)
            print(f"  {cn_name:12s} ({type_name:12s}): {total}")
        except Exception as e:
            print(f"  {cn_name:12s} ({type_name:12s}): 失败 - {e}")


def test_tags(api, user_id):
    """获取标签、风格、工作室、年份"""
    print("\n" + "=" * 60)
    print("2. 标签 / 风格 / 工作室 / 年份")
    print("=" * 60)

    # 标签 Tags
    print("\n  --- 标签 (Tags) ---")
    try:
        tags = api._get("Tags", params={"UserId": user_id})
        if isinstance(tags, dict):
            items = tags.get("Items", [])
            total = tags.get("TotalRecordCount", len(items))
            print(f"  标签总数: {total}")
            for t in items[:30]:
                print(f"    - {t.get('Name', t)}")
            if total > 30:
                print(f"    ... 还有 {total - 30} 个")
        elif isinstance(tags, list):
            print(f"  标签总数: {len(tags)}")
            for t in tags[:30]:
                print(f"    - {t if isinstance(t, str) else t.get('Name', t)}")
            if len(tags) > 30:
                print(f"    ... 还有 {len(tags) - 30} 个")
    except Exception as e:
        print(f"  失败: {e}")

    # 风格 Genres
    print("\n  --- 风格 (Genres) ---")
    try:
        genres = api._get("Genres", params={"UserId": user_id, "Limit": 50})
        if isinstance(genres, dict):
            items = genres.get("Items", [])
            total = genres.get("TotalRecordCount", len(items))
            print(f"  风格总数: {total}")
            for g in items[:40]:
                print(f"    - {g.get('Name', 'Unknown')}")
            if total > 40:
                print(f"    ... 还有 {total - 40} 个")
        elif isinstance(genres, list):
            print(f"  风格总数: {len(genres)}")
            for g in genres[:40]:
                print(f"    - {g if isinstance(g, str) else g.get('Name', g)}")
    except Exception as e:
        print(f"  失败: {e}")

    # 工作室 Studios
    print("\n  --- 工作室 (Studios) ---")
    try:
        studios = api._get("Studios", params={"UserId": user_id, "Limit": 30})
        if isinstance(studios, dict):
            items = studios.get("Items", [])
            total = studios.get("TotalRecordCount", len(items))
            print(f"  工作室总数: {total}")
            for s in items[:30]:
                print(f"    - {s.get('Name', 'Unknown')}")
            if total > 30:
                print(f"    ... 还有 {total - 30} 个")
    except Exception as e:
        print(f"  失败: {e}")

    # 年份 Years
    print("\n  --- 年份 (Years) ---")
    try:
        years = api._get("Years", params={
            "UserId": user_id,
            "SortBy": "SortName",
            "SortOrder": "Descending",
            "Limit": 30,
        })
        if isinstance(years, dict):
            items = years.get("Items", [])
            total = years.get("TotalRecordCount", len(items))
            print(f"  年份总数: {total}")
            for y in items[:30]:
                print(f"    - {y.get('Name', 'Unknown')}")
    except Exception as e:
        print(f"  失败: {e}")


def test_music(api, user_id):
    """获取音乐详情 - 歌手、专辑、歌曲"""
    print("\n" + "=" * 60)
    print("3. 音乐详情")
    print("=" * 60)

    # 歌手/艺术家
    print("\n  --- 歌手/艺术家 ---")
    try:
        artists = api._get("Artists", params={
            "UserId": user_id,
            "Limit": 50,
            "SortBy": "SortName",
            "SortOrder": "Ascending",
        })
        if isinstance(artists, dict):
            items = artists.get("Items", [])
            total = artists.get("TotalRecordCount", len(items))
            print(f"  歌手总数: {total}")
            for a in items[:50]:
                name = a.get("Name", "Unknown")
                album_count = a.get("AlbumCount", 0) or 0
                song_count = a.get("SongCount", 0) or 0
                print(f"    - {name}  (专辑:{album_count}, 歌曲:{song_count})")
            if total > 50:
                print(f"    ... 还有 {total - 50} 个歌手")
    except Exception as e:
        print(f"  失败: {e}")

    # 音乐专辑
    print("\n  --- 音乐专辑 ---")
    try:
        albums = api._get("Items", params={
            "UserId": user_id,
            "IncludeItemTypes": "MusicAlbum",
            "Recursive": "true",
            "Limit": 30,
            "SortBy": "SortName",
            "Fields": "AlbumArtist",
        })
        if isinstance(albums, dict):
            total = albums.get("TotalRecordCount", 0)
            items = albums.get("Items", [])
            print(f"  专辑总数: {total}")
            for a in items[:30]:
                artist = a.get("AlbumArtist", "")
                print(f"    - {a.get('Name', 'Unknown')} {'by ' + artist if artist else ''}")
            if total > 30:
                print(f"    ... 还有 {total - 30} 个专辑")
    except Exception as e:
        print(f"  失败: {e}")


def test_movies_and_series(api, user_id):
    """获取电影和电视剧"""
    print("\n" + "=" * 60)
    print("4. 电影 & 电视剧")
    print("=" * 60)

    # 电影
    try:
        movies = api._get("Items", params={
            "UserId": user_id,
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "Limit": 30,
            "SortBy": "DateCreated",
            "SortOrder": "Descending",
            "Fields": "ProductionYear,CommunityRating,Genres,Studios,RunTimeTicks",
        })
        if isinstance(movies, dict):
            total = movies.get("TotalRecordCount", 0)
            items = movies.get("Items", [])
            print(f"\n  电影总数: {total}")
            for m in items[:30]:
                name = m.get("Name", "Unknown")
                year = m.get("ProductionYear", "")
                rating = m.get("CommunityRating", "")
                genres = ", ".join(m.get("Genres", []))
                runtime_ticks = m.get("RunTimeTicks", 0) or 0
                runtime_min = round(runtime_ticks / 600000000) if runtime_ticks else ""
                print(f"    - {name} ({year}) 评分:{rating} [{genres}] {str(runtime_min) + 'min' if runtime_min else ''}")
            if total > 30:
                print(f"    ... 还有 {total - 30} 部")
    except Exception as e:
        print(f"  获取电影失败: {e}")

    # 电视剧
    try:
        series = api._get("Items", params={
            "UserId": user_id,
            "IncludeItemTypes": "Series",
            "Recursive": "true",
            "Limit": 30,
            "SortBy": "DateCreated",
            "SortOrder": "Descending",
            "Fields": "ProductionYear,CommunityRating,Genres",
        })
        if isinstance(series, dict):
            total = series.get("TotalRecordCount", 0)
            items = series.get("Items", [])
            print(f"\n  电视剧总数: {total}")
            for s in items[:30]:
                name = s.get("Name", "Unknown")
                year = s.get("ProductionYear", "")
                rating = s.get("CommunityRating", "")
                genres = ", ".join(s.get("Genres", []))
                print(f"    - {name} ({year}) 评分:{rating} [{genres}]")
            if total > 30:
                print(f"    ... 还有 {total - 30} 部")
    except Exception as e:
        print(f"  获取电视剧失败: {e}")


def main():
    api, user_id = create_client()
    print(f"已连接, 用户ID: {user_id}\n")

    test_item_counts(api, user_id)
    test_tags(api, user_id)
    test_music(api, user_id)
    test_movies_and_series(api, user_id)

    print("\n========== 所有统计完成! ==========")


if __name__ == "__main__":
    main()
