# Jellyfin Agent 通信协议与渲染指南

## 概览

服务端提供四种交互模式，客户端根据场景选择：

| 模式 | 端点 | 特点 |
|------|------|------|
| SSE 流式对话 | `GET/POST /ask_stream` | 实时流式，支持多轮对话，逐 token 推送 |
| 结构化推荐 | `POST /recommend` | 一次性返回完整结果，含推荐理由 |
| 意图分析 | `POST /intent` | LLM 解析意图 → 调工具 → 返回结果 |
| 直通 API | `GET /search, /detail, ...` | 不走 LLM，直接查 Jellyfin |

> **SSE 详细协议**：参见 [docs/SSE_DETAIL.md](docs/SSE_DETAIL.md)（完整协议）和 [docs/SSE_QUICK_REF.md](docs/SSE_QUICK_REF.md)（快速参考）

---

## 一、SSE 流式对话 (`/ask_stream`)

同时支持 POST 和 GET：

| 方法 | 参数方式 | 用途 |
|------|---------|------|
| `POST /ask_stream` | JSON body | 前端 fetch / axios 调用 |
| `GET /ask_stream` | query string | 浏览器原生 `EventSource` |

### POST 请求

```json
{
  "question": "推荐3部科幻电影",
  "session_id": "可选，传入则继续之前的对话"
}
```

### GET 请求

```
GET /ask_stream?question=推荐3部科幻电影&session_id=可选
```

浏览器 EventSource 示例：
```js
const es = new EventSource('/ask_stream?question=推荐3部电影');
es.addEventListener('token', e => { /* 追加文本 */ });
es.addEventListener('card', e => { /* 渲染卡片 */ });
es.addEventListener('done', e => { es.close(); });
```

### 事件流顺序

```
event: thinking    ← Agent 开始思考（显示"思考中..."）
event: tool        ← 工具调用中（显示"正在搜索..."）
event: tool        ← 工具返回结果（status: "done"）
event: thinking    ← LLM 开始生成文本
event: token       ← "根"     ← 逐 token 推送，客户端拼接到文本区
event: token       ← "据"
...
event: thinking    ← 正在生成推荐理由
event: card        ← 最终推荐卡片（仅 id + reason，客户端用 id 查详情）
event: card        ← ...更多卡片
event: session     ← 会话信息（session_id）
event: done        ← 结束信号（含完整 answer + 所有卡片）
```

> **关键变更**：`card` 事件只在最终推荐阶段发送（不在搜索中间过程），包含 `id` + `reason` + `type`。
> 客户端拿到 `id` 后调用 `getMediaItemDetail(id)` 从 Jellyfin 获取完整数据（海报、名称、评分等）。
> `type` 字段帮助前端决定跳转到哪个详情页（如 `movie`/`series`/`audio`/`musicalbum`/`musicartist` 等）。

### 事件格式概览

| 事件 | 数据格式 | 说明 |
|------|---------|------|
| `thinking` | `{"node": "llm"}` | `llm`=LLM生成 / `reason`=生成推荐理由 |
| `tool` | `{"tool":"...","status":"calling/done","args":{},"preview":"..."}` | 工具调用状态 |
| `token` | `{"content": "根"}` | 逐字文本，客户端拼接为 markdown |
| `card` | `{"id":"abc123","reason":"推荐理由","type":"video"}` | 最终推荐，id+reason+type |
| `session` | `{"session_id":"a1b2c3","history_count":4}` | 会话信息 |
| `done` | `{"answer":"...","cards":[...],"session_id":"..."}` | 结束信号 |

### card 事件

```json
{"id": "5269cdea534b8be612b06bdf8fc73a3b", "reason": "影史经典，讲述希望与自由的永恒主题", "type": "video"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | Jellyfin Item ID，客户端用它查询完整数据 |
| `reason` | string | LLM 生成的推荐理由 |
| `type` | string | 卡片类型，直接透传 Jellyfin 原始类型名（小写），帮助前端决定跳转到哪个详情页。可选值见下表 |

**type 枚举说明**：

| type 值 | 对应 Jellyfin 类型 | 说明 |
|---------|-------------------|------|
| `movie` | Movie | 电影 |
| `series` | Series | 电视剧 |
| `episode` | Episode | 剧集 |
| `video` | Video | 通用视频 |
| `season` | Season | 季 |
| `audio` | Audio | 歌曲 |
| `musicalbum` | MusicAlbum | 音乐专辑 |
| `musicartist` | MusicArtist | 歌手/艺术家 |
| `musicvideo` | MusicVideo | 音乐视频 |
| `book` | Book | 书籍 |
| `comicbook` | ComicBook | 漫画 |

客户端拿到 `id` 后，通过以下方式获取完整媒体数据：
- 直通 API：`GET /detail?item_id={id}`
- 或直接调 Jellyfin API：`GET {JELLYFIN_URL}/Users/{user_id}/Items/{id}`

### done 事件

```json
{
  "answer": "根据您的要求，推荐3部经典科幻电影：...",
  "cards": [
    {"id": "5269cdea534b8be612b06bdf8fc73a3b", "reason": "影史经典...", "type": "video"},
    {"id": "4626c39dcf0b8e2a4b127ae39ba4a689", "reason": "黑帮巅峰...", "type": "video"}
  ],
  "session_id": "a1b2c3d4"
}
```

### 客户端渲染策略

**核心原则：文字逐字涌现，卡片在文本之后出现，客户端用 id 自查详情。**

1. 收到 `thinking` → 显示 "思考中..." 动画
2. 收到 `tool` (calling) → 显示 "正在搜索..."
3. 收到 `tool` (done) → 隐藏 "正在搜索..."
4. 收到 `token` → **追加到文本区域**，实时渲染 markdown
5. 收到 `card` → 拿到 `id` + `reason` + `type`，根据 `type`（如 `movie`、`audio`、`musicartist`）决定跳转目标页，调 `getMediaItemDetail(id)` 获取完整数据后渲染卡片
6. 收到 `done` → 兜底补全，关闭 SSE 连接

**推荐布局：**
```
┌─────────────────────────────────────┐
│ [状态栏] 思考中... / 正在搜索...      │
├─────────────────────────────────────┤
│  根据您的要求，推荐3部经典科幻电影：   │  ← token 逐字出现
│  1. **肖申克的救赎** — 蒂姆·罗宾斯... │
├─────────────────────────────────────┤
│ ┌─────┐ ┌─────┐ ┌─────┐           │  ← card 推送 id，客户端查详情渲染
│ │ 🎬  │ │ 🎬  │ │ 🎬  │           │     海报/名称/评分/年份等
│ │肖申克│ │教父  │ │阿甘  │           │
│ │8.7⭐│ │8.69⭐│ │8.47⭐│           │
│ │影史  │ │黑帮  │ │励志  │           │  ← reason 直接显示
│ │经典  │ │巅峰  │ │传奇  │           │
│ └─────┘ └─────┘ └─────┘           │
└─────────────────────────────────────┘
```

### 多轮对话

```
第一次请求:
POST /ask_stream  {"question": "推荐科幻电影"}
→ 收到 session: {"session_id": "abc123"}

第二次请求（继续对话）:
POST /ask_stream  {"question": "其中哪部最好看", "session_id": "abc123"}
→ Agent 会看到之前的历史
```

- 最多 10 轮对话（20 条消息），超出自动丢弃最早的
- 最多 100 个并发会话

---

## 二、结构化推荐 (`POST /recommend`)

不走 Agent 流式，直接返回 JSON。

### 请求

```json
{"question": "推荐5部评分8分以上的动作片"}
```

### 响应

```json
{
  "question": "推荐5部评分8分以上的动作片",
  "items": [
    {
      "id": "xxx",
      "name": "蝙蝠侠：黑暗骑士",
      "type": "Movie",
      "year": 2008,
      "rating": 8.8,
      "genres": ["Action", "Crime", "Drama"],
      "overview": "哥谭市最黑暗的时刻...",
      "runtimeMinutes": 152,
      "posterUrl": "http://localhost:8096/Items/xxx/Images/Primary",
      "reason": "希斯莱杰绝世小丑，DC最佳电影",
      "cardType": "movie",
      "people": [...],
      "studios": [...],
      "played": false,
      "playCount": 0,
      "favorite": false,
      "positionMinutes": 0
    }
  ],
  "total": 5
}
```

- `items` 包含完整媒体数据（与直通 API `/search` 格式一致）
- `reason` 已填充
- `cardType` 字段标识卡片类型：`movie` / `series` / `audio` / `musicalbum` / `musicartist` / `book` 等（Jellyfin 原始类型名小写）
- 空结果时 `items: []`, `total: 0`

---

## 三、意图分析 (`POST /intent`)

### 请求

```json
{"question": "星际穿越讲了什么"}
```

### 响应

```json
{
  "question": "星际穿越讲了什么",
  "intent": {
    "tool": "get_item_overview",
    "args": {"keyword": "星际穿越"}
  },
  "result": "星际穿越:\n在不久的将来，地球面临末日危机..."
}
```

- `result` 是工具返回的纯文本字符串
- `intent.tool` 可能的值：`search_media`, `get_item_detail`, `get_item_overview`, `get_items_overview`, `get_episodes`, `get_album_tracks`, `get_play_status`, `get_genres`, `get_years`, `get_libraries`, `get_media_stats`, `get_next_up`, `get_resume_items`, `get_latest`, `search_artists`, `search_songs_by_artist`, `get_similar`, `get_lyrics`

---

## 四、直通 API（GET，不走 LLM）

### 搜索

```
GET /search?keyword=星际&media_type=Movie&genres=Sci-Fi&min_rating=8&limit=10&sort_by=CommunityRating&sort_order=Descending
```

```json
{
  "items": [{"id":"xxx","name":"星际穿越","type":"Movie","year":2014,"rating":8.6,...}],
  "total": 3
}
```

### 详情

```
GET /detail?keyword=星际穿越
GET /detail?item_id=xxx
```

```json
{"result": "=== 星际穿越 (Movie) ===\n年份: 2014\n评分: 8.6\n..."}
```

- `result` 是格式化纯文本

### 简介

```
GET /overview?keyword=星际穿越
```

### 批量简介

```
GET /overviews?media_type=Movie&genres=Sci-Fi&limit=5
```

### 剧集列表

```
GET /episodes?keyword=权力的游戏&season=1
```

### 专辑歌曲

```
GET /tracks?keyword=周杰伦
```

### 播放状态

```
GET /play_status?media_type=Movie&filter=unplayed&limit=10
```

- `filter`：`all` / `unplayed` / `played` / `favorite`

### 下一集（追剧）

```
GET /next_up?limit=10
```

返回结构化 JSON：`{"items": [...], "total": N}`，包含海报 URL。

### 继续播放

```
GET /resume?media_type=Video&limit=10
```

- `media_type`：`Video`（默认）/ `Audio`
- 返回结构化 JSON：`{"items": [...], "total": N}`

### 最新添加

```
GET /latest?media_type=Movie&limit=10
```

- `media_type`：`Movie` / `Series` / `Audio` / `Book` 等，留空=全部
- 返回结构化 JSON：`{"items": [...], "total": N}`

### 搜索歌手

```
GET /artists?keyword=周杰伦&limit=20
```

返回：`{"items": [{"id":"...", "name":"...", "type":"MusicArtist"}]}`

### 按歌手搜歌

```
GET /songs_by_artist?artist=周杰伦&limit=5
```

返回：`{"result": "歌手 '周杰伦' 的歌曲（共 179 首，展示 5 首）:..."}`（格式化文本）

### 相似内容

```
GET /similar?keyword=肖申克&limit=10
GET /similar?item_id=xxx&limit=10
```

返回：`{"result": "..."}`（格式化文本）

### 歌词

```
GET /lyrics?keyword=Mojito
GET /lyrics?item_id=xxx
```

返回：`{"result": "..."}`（纯文本歌词，依赖歌词插件）

### 统计数据

```
GET /genres
GET /years
GET /libraries
GET /stats
```

均返回 `{"result": "..."}` 纯文本。

### 海报图片

海报 URL 格式：
```
{JELLYFIN_URL}/Items/{id}/Images/Primary
```

- 直接作为 `<img src>` 使用
- 无需额外认证（Jellyfin 默认公开图片）
- 如需指定尺寸：`/Images/Primary?maxHeight=300&maxWidth=200`

---

## 五、其他端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查，返回 `{"status":"ok"}` |
| POST | `/sessions` | 列出所有活跃会话 |
| DELETE | `/sessions/{id}` | 删除指定会话 |
| POST | `/refresh_cache` | 强制刷新缓存（genres/libraries 等） |

---

## 六、错误处理

所有接口错误返回格式：

```json
{
  "error": "错误描述",
  "traceback": "仅在 /intent 和 /recommend 中，服务端异常时附带"
}
```

HTTP 状态码：
- `200`：成功
- `400`：参数错误（如未知工具名）
- `404`：资源不存在（如删除不存在的会话）
- `500`：服务端异常

---

## 七、类型枚举参考

### media_type

| 值 | 含义 |
|----|------|
| `Movie` | 电影 |
| `Series` | 电视剧 |
| `Audio` | 歌曲 |
| `Book` | 书籍 |
| `MusicAlbum` | 音乐专辑 |

### sort_by

| 值 | 含义 |
|----|------|
| `CommunityRating` | 社区评分（默认） |
| `DateCreated` | 添加时间 |
| `SortName` | 名称排序 |
| `ProductionYear` | 上映年份 |

### sort_order

| 值 | 含义 |
|----|------|
| `Descending` | 降序（默认） |
| `Ascending` | 升序 |

### status_filter（播放状态）

| 值 | 含义 |
|----|------|
| `all` | 全部 |
| `unplayed` | 未看 |
| `played` | 已看 |
| `favorite` | 已收藏 |
