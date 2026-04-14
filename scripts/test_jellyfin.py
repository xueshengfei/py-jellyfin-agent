"""测试 jellyfin-apiclient-python 库是否能正常连接 Jellyfin 服务器"""

from jellyfin_apiclient_python import JellyfinClient
from jellyfin_apiclient_python.connection_manager import ConnectionManager
from jellyfin_apiclient_python.api import API


def main():
    server_url = "http://localhost:8096"
    username = "xue13"
    password = "123456"

    # 1. 创建客户端
    client = JellyfinClient()
    client.config.app("test-app", "0.0.1", "test-device", "device-id-001")

    # 初始化必要的配置字段
    client.config.data["auth.ssl"] = False
    client.config.data["auth.server"] = server_url
    client.config.data["auth.server-id"] = ""
    client.config.data["auth.token"] = ""
    client.config.data["auth.user_id"] = ""

    # 2. 创建连接管理器
    cm = ConnectionManager(client)

    # 3. 连接服务器
    print(f"正在连接 Jellyfin 服务器: {server_url}")
    try:
        result = cm.connect_to_address(server_url)
        state = result.get("State", "")
        print(f"连接服务器成功, 状态: {state}")
    except Exception as e:
        print(f"连接服务器失败: {e}")

    # 4. 登录
    try:
        auth_result = cm.login(server_url, username, password)
        print(f"\n登录成功!")
        user_info = auth_result.get("User", {})
        user_id = user_info.get("Id", "N/A")
        token = auth_result.get("AccessToken", "")
        print(f"  用户ID: {user_id}")
        print(f"  用户名: {user_info.get('Name', 'N/A')}")
        print(f"  Access Token: {token[:20]}..." if token else "  Access Token: N/A")
    except Exception as e:
        print(f"登录失败: {e}")
        return

    # 更新客户端认证信息
    client.config.data["auth.token"] = token
    client.config.data["auth.user_id"] = user_id
    client.config.data["auth.server"] = server_url

    # 5. 创建 API 实例
    api = API(client.http)

    # 6. 测试获取系统信息
    print("\n--- 测试 get_system_info ---")
    try:
        system_info = api.get_system_info()
        print(f"  服务器版本: {system_info.get('Version', 'N/A')}")
        print(f"  操作系统: {system_info.get('OperatingSystem', 'N/A')}")
        print(f"  服务器名称: {system_info.get('ServerName', 'N/A')}")
    except Exception as e:
        print(f"  失败: {e}")

    # 7. 测试获取媒体库
    print("\n--- 测试 get_views ---")
    try:
        views = api.get_views()
        items = views.get("Items", [])
        print(f"  媒体库 ({len(items)} 个):")
        for item in items:
            print(f"    - {item.get('Name', 'Unknown')} (类型: {item.get('CollectionType', 'N/A')})")
    except Exception as e:
        print(f"  失败: {e}")

    # 8. 测试获取最近添加
    print("\n--- 测试 get_recently_added ---")
    try:
        recent = api.get_recently_added(limit=5)
        items = recent.get("Items", [])
        print(f"  最近添加 ({len(items)} 个):")
        for item in items:
            name = item.get("Name", "Unknown")
            item_type = item.get("Type", "Unknown")
            year = item.get("ProductionYear", "")
            print(f"    - {name} ({item_type}) {year}")
    except Exception as e:
        print(f"  失败: {e}")

    # 9. 测试获取媒体文件夹
    print("\n--- 测试 get_media_folders ---")
    try:
        folders = api.get_media_folders()
        items = folders.get("Items", [])
        print(f"  媒体文件夹 ({len(items)} 个):")
        for item in items:
            print(f"    - {item.get('Name', 'Unknown')} (ID: {item.get('Id', 'N/A')})")
    except Exception as e:
        print(f"  失败: {e}")

    # 10. 测试获取用户信息
    print("\n--- 测试 get_user ---")
    try:
        user = api.get_user(user_id)
        print(f"  用户名: {user.get('Name', 'N/A')}")
        print(f"  是否管理员: {user.get('Policy', {}).get('IsAdministrator', False)}")
    except Exception as e:
        print(f"  失败: {e}")

    print("\n========== 所有测试完成! ==========")


if __name__ == "__main__":
    main()
