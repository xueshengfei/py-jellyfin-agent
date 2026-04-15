# Jellyfin Agent - AI 媒体助手

基于 LangChain + DeepSeek 的 Jellyfin 智能 Agent，用自然语言与你的媒体库对话。

搜索影片、获取推荐、查看播放进度、浏览剧集和音乐——一句话搞定。

## 功能特性

- **自然语言交互** - 用中文或英文提问，Agent 自动调用合适的工具
- **SSE 流式输出** - 逐 token 推送，前端实时渲染
- **多轮对话** - 基于 session 的上下文记忆，支持连续追问
- **20 个专业工具** - 覆盖搜索、详情、剧集、音乐、播放状态、推荐等
- **前端卡片支持** - JSON 格式返回完整媒体数据，可直接渲染为 UI 卡片
- **启动缓存预热** - genres / libraries / years / stats 首次加载即缓存

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
| LLM | DeepSeek (兼容 OpenAI 接口) |
| 媒体服务 | Jellyfin API |
| 通信协议 | SSE + REST |

## 项目结构

```
py_jellyfin/
├── main.py                 # 启动入口
├── server/
│   └── app.py              # FastAPI 路由 & SSE 流式处理
├── agent/
│   └── core.py             # LangChain ReAct Agent 定义
├── client/
│   └── jellyfin.py         # Jellyfin API 客户端 + 20 个 Tool
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
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
JELLYFIN_URL=http://your-jellyfin-server:8096
JELLYFIN_USERNAME=your_username
JELLYFIN_PASSWORD=your_password
```

### 3. 启动服务

```bash
python main.py
```

服务启动在 `http://localhost:5000`。

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

### 前端示例

```js
const es = new EventSource('/ask_stream?question=推荐3部科幻电影');

es.addEventListener('thinking', () => showLoading());
es.addEventListener('tool', e => {
  const { status, preview } = JSON.parse(e.data);
  status === 'calling' ? showToolStatus(preview) : hideToolStatus();
});
es.addEventListener('token', e => {
  const { content } = JSON.parse(e.data);
  textArea.textContent += content;  // 逐字追加
});
es.addEventListener('card', e => {
  const { id, reason } = JSON.parse(e.data);
  fetch(`/detail?item_id=${id}`).then(r => r.json())  // 查详情
    .then(detail => renderCard({ ...detail, reason }));  // 渲染卡片
});
es.addEventListener('done', () => es.close());
```

## Agent 工具一览

Agent 拥有 20 个工具，按功能分类：

| 类别 | 工具 | 说明 |
|------|------|------|
| 搜索 | `search_media` / `search_media_json` | 搜索媒体，JSON 版返回卡片数据 |
| 搜索 | `search_artists` | 搜索歌手 |
| 搜索 | `search_songs_by_artist` / `_json` | 按歌手搜歌 |
| 详情 | `get_item_detail` / `get_item_overview` / `get_items_overview` | 元数据 / 简介 / 批量简介 |
| 剧集/音乐 | `get_episodes` / `get_album_tracks` / `get_lyrics` | 剧集列表 / 曲目 / 歌词 |
| 状态 | `get_play_status` / `get_next_up` / `get_resume_items` / `get_latest` | 播放状态 / 下一集 / 继续 / 最新 |
| 发现 | `get_similar` / `get_genres` / `get_years` / `get_libraries` / `get_media_stats` | 相似 / 分类 / 年份 / 媒体库 / 统计 |

## 示例对话

```
用户: 帮我找一部诺兰导演的电影
Agent: 我找到了以下克里斯托弗·诺兰导演的电影...

用户: 讲了什么？
Agent: [调用 get_item_overview 获取简介] 这部电影讲述的是...

用户: 类似的电影还有哪些？
Agent: [调用 get_similar] 为你找到了这些类似的电影...
```

## License

MIT
