# 系统架构图

本文档包含 3 张 Mermaid 图，帮助开发者快速理解系统结构、数据流和工具分类。

---

## 图 1：系统架构图

```mermaid
graph TD
    subgraph 客户端层
        Flutter["Flutter App"]
        Browser["Browser / curl"]
    end

    subgraph 服务层 ["服务层 (FastAPI)"]
        Main["main.py<br/>uvicorn 启动"]
        App["server/app.py<br/>路由 + SSE + 会话管理"]
        Agent["agent/core.py<br/>LangChain ReAct Agent<br/>+ System Prompt"]
    end

    subgraph 数据层
        Jellyfin["Jellyfin Server"]
        LLM["LLM API<br/>(DeepSeek)"]
    end

    Flutter -->|"SSE /ask_stream<br/>POST /recommend<br/>POST /intent<br/>GET /search..."| App
    Browser -->|"HTTP API"| App
    Main -->|"uvicorn.run"| App
    App -->|"create_agent()"| Agent
    Agent -->|"astream_events<br/>ChatOpenAI"| LLM
    Agent -->|"@tool 调用"| Client["client/jellyfin.py<br/>20 个 @tool<br/>safe_get + 缓存"]
    Client -->|"REST API<br/>safe_get (401 重连)"| Jellyfin

    style Flutter fill:#4FC3F7,color:#000
    style Browser fill:#4FC3F7,color:#000
    style App fill:#FF8A65,color:#000
    style Agent fill:#FF8A65,color:#000
    style Client fill:#FF8A65,color:#000
    style Main fill:#FF8A65,color:#000
    style Jellyfin fill:#81C784,color:#000
    style LLM fill:#81C784,color:#000
```

### 四种交互模式

| 模式 | 端点 | 说明 |
|------|------|------|
| **SSE** | `/ask_stream` | ReAct Agent 流式对话，逐 token 推送 |
| **Recommend** | `/recommend` | LLM 提取参数 → 搜索 → 生成推荐理由 |
| **Intent** | `/intent` | LLM 意图分析 → 直接调工具 → 返回结果 |
| **Direct API** | `/search`, `/detail`, `/episodes` ... | 直接调工具，不走 LLM |

---

## 图 2：SSE 流程图

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server (app.py)
    participant A as Agent (core.py)
    participant L as LLM (DeepSeek)
    participant T as Tool (jellyfin.py)
    participant J as Jellyfin API

    C->>S: POST /ask_stream {question, session_id}
    S->>S: get_or_create_session()
    S->>A: create_agent()
    A-->>S: agent 实例

    Note over S,J: astream_events 流式循环

    S->>L: on_chat_model_start
    S-->>C: SSE event: thinking {node: "llm"}

    loop LLM 逐 token 生成
        L-->>S: on_chat_model_stream (chunk)
        S-->>C: SSE event: token {content}
    end

    alt LLM 决定调用工具
        S->>T: on_tool_start
        T->>J: safe_get(endpoint, params)
        alt 401 Unauthorized
            J-->>T: 401 error
            T->>J: _reconnect() → safe_get()
        end
        J-->>T: JSON response
        T-->>S: on_tool_end (tool output)
        Note over S: 收集 search_media_json /<br/>search_songs_by_artist_json 结果
        S->>L: 继续生成（工具结果作为上下文）
    end

    Note over S,C: 流结束，生成推荐卡片

    S->>S: _match_final_items()
    S->>L: LLM 生成推荐理由
    S-->>C: SSE event: thinking {node: "reason"}

    loop 每个推荐卡片
        S-->>C: SSE event: card {id, reason}
    end

    S->>S: 保存会话历史
    S-->>C: SSE event: session {session_id, history_count}
    S-->>C: SSE event: done {answer, cards, session_id}
```

---

## 图 3：工具分类图

```mermaid
graph LR
    subgraph 搜索类
        T1["search_media"]
        T2["search_media_json"]
        T3["search_artists"]
        T4["search_songs_by_artist"]
        T5["search_songs_by_artist_json"]
    end

    subgraph 详情类
        T6["get_item_detail"]
        T7["get_item_overview"]
        T8["get_items_overview"]
    end

    subgraph 剧集/音乐
        T9["get_episodes"]
        T10["get_album_tracks"]
        T11["get_lyrics"]
    end

    subgraph 状态/浏览
        T12["get_play_status"]
        T13["get_next_up"]
        T14["get_resume_items"]
        T15["get_latest"]
    end

    subgraph 发现/统计
        T16["get_similar"]
        T17["get_genres"]
        T18["get_years"]
        T19["get_libraries"]
        T20["get_media_stats"]
    end

    style T1 fill:#42A5F5,color:#fff
    style T2 fill:#42A5F5,color:#fff
    style T3 fill:#42A5F5,color:#fff
    style T4 fill:#42A5F5,color:#fff
    style T5 fill:#42A5F5,color:#fff
    style T6 fill:#66BB6A,color:#fff
    style T7 fill:#66BB6A,color:#fff
    style T8 fill:#66BB6A,color:#fff
    style T9 fill:#AB47BC,color:#fff
    style T10 fill:#AB47BC,color:#fff
    style T11 fill:#AB47BC,color:#fff
    style T12 fill:#FFA726,color:#fff
    style T13 fill:#FFA726,color:#fff
    style T14 fill:#FFA726,color:#fff
    style T15 fill:#FFA726,color:#fff
    style T16 fill:#EF5350,color:#fff
    style T17 fill:#EF5350,color:#fff
    style T18 fill:#EF5350,color:#fff
    style T19 fill:#EF5350,color:#fff
    style T20 fill:#EF5350,color:#fff
```

共 20 个 `@tool`，分为 5 个功能类别：

- **搜索类** (蓝色) — 按关键字/歌手搜索媒体，含 JSON 变体供前端卡片渲染
- **详情类** (绿色) — 获取单个或批量条目的完整元数据/简介
- **剧集/音乐** (紫色) — 查询剧集列表、专辑曲目、歌词
- **状态/浏览** (橙色) — 播放状态、追剧下一集、继续播放、最新内容
- **发现/统计** (红色) — 相似推荐、风格/年份/媒体库列表、数量统计
