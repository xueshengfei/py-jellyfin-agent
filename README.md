# Jellyfin Agent - AI 媒体助手

基于 LangChain 的 Jellyfin 智能 Agent，用自然语言与你的媒体库对话。

底层大模型可自由替换（DeepSeek / OpenAI / 其他兼容 OpenAI 接口的模型均可）。

搜索影片、获取推荐、查看播放进度、浏览剧集和音乐——一句话搞定。

## 功能特性

- **自然语言交互** - 用中文或英文提问，Agent 自动分析意图并调用对应的查询工具
- **意图识别 + 工具路由** - Agent 封装了 Jellyfin 常用查询操作，按意图自动匹配到对应的工具
- **SSE 流式输出** - 逐 token 推送，前端实时渲染
- **多轮对话** - 基于 session 的上下文记忆，支持连续追问
- **前端卡片支持** - 返回 item_id + 推荐理由，前端渲染媒体卡片
- **模型可替换** - 兼容任何 OpenAI 接口格式的 LLM，一行配置即可切换

## 工作流

整个系统围绕一条核心管线运行：**用户提问 → 意图分析 → 调用工具查询媒体库 → 返回推荐结果**

```
用户提问: "推荐几部评分高的科幻电影"
        │
        ▼
┌─────────────────┐
│   意图分析 (LLM)  │  Agent 理解用户意图，决定调用哪些工具
└────────┬────────┘
         │
         ▼
┌─────────────────────┐
│  工具调用 (Jellyfin)  │  search_media → get_item_detail → get_similar ...
│  查询媒体库           │  Agent 可多轮调用不同工具，逐步获取完整信息
└────────┬────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│           返回结果                    │
│                                     │
│  token 事件 → 文字回复（逐字推送）    │
│  card  事件 → 推荐卡片列表           │
│    ├─ item_id  → 媒体 ID            │
│    └─ reason   → LLM 生成的推荐理由  │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  前端渲染                            │
│  ├─ 文字区：拼接 token → markdown    │
│  └─ 卡片区：拿 item_id 查详情        │
│     → 海报 / 名称 / 评分 / 年份 ...  │
└─────────────────────────────────────┘
```

关键点：

- **意图分析**由 LLM 完成，Agent（ReAct 模式）自主判断需要调用哪些工具、以什么顺序调用
- **工具调用**可能多轮执行，例如先搜索 → 再取详情 → 再查相似内容
- **最终输出**分两部分：文字回复（token 流）+ 推荐卡片（item_id + reason），前端用 item_id 向 Jellyfin 查询海报、名称、评分等完整数据来渲染卡片

## 技术栈

| 层级 | 技术 |
|------|------|
| Web 框架 | FastAPI + Uvicorn |
| AI Agent | LangChain ReAct Agent |
| LLM | 任意兼容 OpenAI 接口的模型（默认 DeepSeek） |
| 媒体服务 | Jellyfin API |
| 通信协议 | SSE + REST |

### 如何更换大模型

项目通过 LangChain 的 `ChatOpenAI` 接入 LLM，只需修改 `.env` 中的两个变量即可切换模型：

```env
# 示例：切换到 OpenAI
LLM_API_KEY=sk-xxxxx
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o
```

然后在 `agent/core.py` 中修改对应的读取方式：

```python
# agent/core.py — create_agent() 函数
llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "deepseek-chat"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
    api_key=os.getenv("LLM_API_KEY"),
)
```

任何兼容 OpenAI Chat Completions 接口的模型都可以直接接入，包括但不限于：

- DeepSeek (`deepseek-chat`)
- OpenAI (`gpt-4o`, `gpt-4o-mini`)
- 通义千问 (`qwen-plus`)
- GLM (`glm-4`)
- 本地模型（通过 Ollama / vLLM 等部署的兼容端点）

## 项目结构

```
py_jellyfin/
├── main.py                 # 启动入口
├── server/
│   └── app.py              # FastAPI 路由 & SSE 流式处理
├── agent/
│   └── core.py             # LangChain ReAct Agent 定义
├── client/
│   └── jellyfin.py         # Jellyfin 查询工具封装
├── docs/                   # 协议文档
├── .env                    # 环境变量配置
├── requirements.txt        # Python 依赖
└── start.bat               # Windows 一键启动
```

## 快速开始

### 1. 安装依赖

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

或直接双击 `start.bat`（Windows 自动创建 venv 并安装依赖）。

### 2. 配置环境变量

复制并编辑 `.env` 文件：

```env
# 大模型配置（支持任何兼容 OpenAI 接口的模型）
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# Jellyfin 配置
JELLYFIN_URL=http://your-jellyfin-server:8096
JELLYFIN_USERNAME=your_username
JELLYFIN_PASSWORD=your_password
```

### 3. 启动服务

```bash
python main.py
```

服务启动在 `http://localhost:5005`。

## API 接口

唯一的前端入口：`POST /ask_stream`

### 请求

```json
{
  "question": "推荐几部评分高的科幻电影",
  "session_id": "可选，传入则继续之前的对话"
}
```

也支持 GET（浏览器原生 EventSource）：

```
GET /ask_stream?question=推荐3部科幻电影&session_id=可选
```

### SSE 事件流

一次请求的完整事件流：

```
event: thinking    → "思考中..."          Agent 开始分析意图
event: tool        → "正在搜索..."        调用工具查询 Jellyfin
event: tool        → 搜索完成
event: thinking    → LLM 生成文本
event: token       → "根据"              逐 token 推送文字回复
event: token       → "您的要求"
event: token       → "..."
event: card        → {id, reason}        推荐卡片（item_id + 推荐理由）
event: card        → {id, reason}        更多卡片...
event: session     → {session_id}        会话信息
event: done        → {answer, cards}     结束，含完整兜底数据
```

### 事件格式

| 事件 | 数据格式 | 用途 |
|------|---------|------|
| `thinking` | `{"node":"llm"}` | 显示思考动画 |
| `tool` | `{"tool":"search_media","status":"calling/done","preview":"..."}` | 显示工具调用状态 |
| `token` | `{"content":"根据"}` | 追加到文字区，渲染 markdown |
| `card` | `{"id":"abc123","reason":"推荐理由"}` | 拿 item_id 查详情，渲染媒体卡片 |
| `session` | `{"session_id":"a1b2c3","history_count":4}` | 保存 session_id 用于多轮对话 |
| `done` | `{"answer":"...","cards":[...],"session_id":"..."}` | 兜底数据，关闭连接 |

### card 事件（重点）

这是前端渲染媒体卡片的核心数据：

```json
{
  "id": "5269cdea534b8be612b06bdf8fc73a3b",
  "reason": "影史经典，讲述希望与自由的永恒主题"
}
```

| 字段 | 说明 |
|------|------|
| `id` | Jellyfin 媒体 Item ID，前端用它查询海报、名称、评分、年份等完整数据 |
| `reason` | LLM 生成的推荐理由，直接展示给用户 |

前端拿到 `id` 后，通过 `GET /detail?item_id={id}` 获取完整媒体数据来渲染卡片。

### 多轮对话

```
第一次请求:
POST /ask_stream  {"question": "推荐科幻电影"}
→ 收到 session: {"session_id": "abc123"}

第二次请求:
POST /ask_stream  {"question": "其中哪部最好看", "session_id": "abc123"}
→ Agent 能看到之前的对话历史，继续上下文回答
```

最多 10 轮对话（20 条消息），最多 100 个并发会话。

## Agent 工具体系

Agent 将 Jellyfin 的查询操作封装为 6 大类工具。当用户提问时，LLM 会自动识别意图，路由到对应类别的工具执行查询：

```
用户提问
    │
    ▼
┌──────────┐
│ 意图识别  │  LLM 判断用户想做什么
└────┬─────┘
     │
     ├── "找电影/电视剧/音乐"  ──→  搜索类工具
     ├── "讲了什么/详细信息"   ──→  详情类工具
     ├── "有哪些集/什么歌"     ──→  剧集音乐类工具
     ├── "看到哪了/下一集"     ──→  播放状态类工具
     ├── "类似的还有吗"        ──→  推荐发现类工具
     └── "有哪些分类/统计"     ──→  媒体库统计类工具
```

### 搜索类

按关键词、类型、评分、年份等条件搜索媒体库，`_json` 后缀版本返回结构化数据供前端渲染卡片。

| 用户意图示例 | 对应工具 |
|-------------|---------|
| "搜一下星际穿越" / "找几部动作片" | `search_media` / `search_media_json` |
| "周杰伦有哪些歌" | `search_artists` / `search_songs_by_artist` / `search_songs_by_artist_json` |

### 详情类

获取单个或多个媒体的完整元数据（名称、评分、演员、时长、简介等）。

| 用户意图示例 | 对应工具 |
|-------------|---------|
| "这部电影的详细信息" | `get_item_detail` |
| "讲了什么" / "剧情简介" | `get_item_overview` / `get_items_overview` |

### 剧集音乐类

深入查询单个媒体的子内容——电视剧的剧集列表、专辑的曲目、歌曲歌词。

| 用户意图示例 | 对应工具 |
|-------------|---------|
| "第一季有哪些集" | `get_episodes` |
| "这张专辑有什么歌" | `get_album_tracks` |
| "这首歌的歌词" | `get_lyrics` |

### 播放状态类

查询用户的观看/收听进度、追剧状态、最近添加的内容。

| 用户意图示例 | 对应工具 |
|-------------|---------|
| "我看到哪了" / "哪些没看完" | `get_play_status` / `get_resume_items` |
| "下一集是什么" | `get_next_up` |
| "最近加了什么新片" | `get_latest` |

### 推荐发现类

基于已有内容发现相似推荐。

| 用户意图示例 | 对应工具 |
|-------------|---------|
| "和这部类似的电影" / "还推荐什么" | `get_similar` |

### 媒体库统计类

获取媒体库的整体概览——分类、年份分布、各媒体库内容统计。

| 用户意图示例 | 对应工具 |
|-------------|---------|
| "有哪些分类" / "什么类型多" | `get_genres` |
| "库里有哪几年的电影" | `get_years` |
| "我的媒体库里都有什么" | `get_libraries` / `get_media_stats` |

## 示例对话

```
用户: 帮我找一部诺兰导演的电影
Agent: [意图: 搜索] → search_media
       我找到了以下克里斯托弗·诺兰导演的电影...

用户: 讲了什么？
Agent: [意图: 详情] → get_item_overview
       这部电影讲述的是...

用户: 类似的电影还有哪些？
Agent: [意图: 推荐] → get_similar
       为你找到了这些类似的电影...
```

## License

MIT
