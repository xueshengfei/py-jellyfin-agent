"""Jellyfin Agent 服务启动入口"""

import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5005"))
    print(f"Jellyfin Agent HTTP 服务启动: http://localhost:{port}")
    uvicorn.run("server.app:app", host="0.0.0.0", port=port, reload=True)
