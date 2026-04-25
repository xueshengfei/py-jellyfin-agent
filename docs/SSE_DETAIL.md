# SSE 流式对话 — 完整协议

## 端点

同时支持 POST 和 GET：

| 方法 | 参数方式 | 用途 |
|------|---------|------|
| `POST /ask_stream` | JSON body | 前端 fetch / axios 调用 |
| `GET /ask_stream` | query string | 浏览器原生 `EventSource` |

---

## 请求

**POST:**
```json
{
  "question": "推荐3部科幻电影",
  "session_id": "可选，传入则继续之前的对话"
}
```

**GET:**
```
GET /ask_stream?question=推荐3部科幻电影&session_id=可选
```

浏览器 EventSource 示例：
```js
const es = new EventSource('/ask_stream?question=推荐3部电影');
es.addEventListener('token', e => { document.getElementById('text').textContent += JSON.parse(e.data).content; });
es.addEventListener('card', e => { renderCard(JSON.parse(e.data)); });
es.addEventListener('done', e => { es.close(); });
```

---

## 事件流完整示例

以下是一次完整请求的事件流，按时间顺序排列：

```
event: thinking    ← Agent 开始思考（显示 "思考中..."）
data: {"node": "llm"}

event: tool        ← 工具调用开始（显示 "正在搜索..."）
data: {"tool": "search_media_json", "status": "calling", "args": {"genres": "Sci-Fi", "limit": 3, "media_type": "Movie"}}

event: tool        ← 工具调用完成（隐藏 "正在搜索..."）
data: {"tool": "search_media_json", "status": "done", "preview": "[{\"id\":\"abc\",...}]"}

event: thinking    ← LLM 开始生成文本（隐藏 "思考中..."）
data: {"node": "llm"}

event: token       ← 逐 token 推送，客户端拼接
data: {"content": "根据"}

event: token
data: {"content": "您的"}

event: token
data: {"content": "要求，推荐3部经典科幻电影：\n\n"}

...                ← 持续推送 token

event: thinking    ← 正在生成推荐理由
data: {"node": "reason"}

event: card        ← 第1张推荐卡片（含推荐理由 + 卡片类型）
data: {"id": "5269cdea534b8be612b06bdf8fc73a3b", "reason": "影史经典，讲述希望与自由的永恒主题", "type": "movie"}

event: card        ← 第2张
data: {"id": "4626c39dcf0b8e2a4b127ae39ba4a689", "reason": "黑帮电影巅峰，家族与权力的深刻演绎", "type": "movie"}

event: card        ← 第3张
data: {"id": "5787daf8b4a0f8ce3adc2d6e1d1f1f8a", "reason": "励志传奇，傻人有傻福的温暖人生故事", "type": "movie"}

event: session     ← 会话信息
data: {"session_id": "a1b2c3d4", "history_count": 2}

event: done        ← 结束信号
data: {"answer": "根据您的要求，推荐3部经典科幻电影：...", "cards": [{"id":"5269...","reason":"..."}, ...], "session_id": "a1b2c3d4"}
```

---

## 事件类型详解

### 1. `thinking` — 思考状态

```json
{"node": "llm"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `node` | string | `llm` — LM 生成文本中 / `reason` — 正在生成推荐理由 |

**客户端行为**：显示/切换加载动画。

### 2. `tool` — 工具调用

**调用中：**
```json
{
  "tool": "search_media_json",
  "status": "calling",
  "args": {"genres": "Sci-Fi", "limit": 3, "media_type": "Movie"}
}
```

**调用完成：**
```json
{
  "tool": "search_media_json",
  "status": "done",
  "preview": "[{\"id\":\"abc\",\"name\":\"星际穿越\",...}]"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `tool` | string | 工具名称，如 `search_media_json` |
| `status` | string | `calling` / `done` |
| `args` | object | 调用参数（仅 `calling` 时有） |
| `preview` | string | 结果前 200 字预览（仅 `done` 时有） |

**客户端行为**：显示 "正在搜索..." 或隐藏。

> **注意**：Agent 可能多次调用工具（搜索多次），每次都有 calling/done 一对。

### 3. `token` — 逐字文本推送

```json
{"content": "根"}
```

```json
{"content": "据"}
```

```json
{"content": "您的要求，推荐3部经典科幻电影：\n\n"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | string | LLM 生成的一个文本片段 |

**关键特性**：
- 每个 token 是 LLM 生成的一个片段（一个字、一个词、或一段标点）
- 客户端将所有 `token` 的 `content` **按顺序拼接**，形成完整 markdown 文本
- 实时渲染，用户能看到文字不断涌现
- 可能包含 `\n` 换行符

### 4. `card` — 推荐卡片（最终推荐阶段）

```json
{"id": "5269cdea534b8be612b06bdf8fc73a3b", "reason": "影史经典，讲述希望与自由的永恒主题", "type": "movie"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | Jellyfin Item ID，全局唯一 |
| `reason` | string | LLM 生成的推荐理由（10-20 字） |
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

**关键设计**：
- card **只在最终推荐阶段发送**，不在搜索中间过程发送
- 包含 `id` + `reason` + `type`，不包含 name/year/rating 等冗余字段
- 客户端拿到 `id` 后，调用 `getMediaItemDetail(id)` 从 Jellyfin 获取完整数据
- 客户端根据 `type` 决定点击卡片后跳转到哪个详情页
- card 在所有 token 之后、done 之前推送

**获取完整数据的 Jellyfin API**：
```
GET {JELLYFIN_URL}/Users/{user_id}/Items/{id}
```

或通过本服务直通 API：
```
GET /detail?item_id={id}
```

### 5. `session` — 会话信息

```json
{"session_id": "a1b2c3d4", "history_count": 4}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 会话 ID，下次请求传入可继续对话 |
| `history_count` | int | 当前会话已有消息数（上限 20 条 = 10 轮） |

### 6. `done` — 结束信号

```json
{
  "answer": "根据您的要求，推荐3部经典科幻电影：\n\n## 1. 肖申克的救赎...",
  "cards": [
    {"id": "5269cdea534b8be612b06bdf8fc73a3b", "reason": "影史经典，讲述希望与自由的永恒主题", "type": "movie"},
    {"id": "4626c39dcf0b8e2a4b127ae39ba4a689", "reason": "黑帮电影巅峰，家族与权力的深刻演绎", "type": "movie"}
  ],
  "session_id": "a1b2c3d4"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `answer` | string | 完整的 LLM 回复文本（与 token 拼接结果一致） |
| `cards` | array | 所有推荐卡片的 `id` + `reason` + `type` 数组 |
| `session_id` | string | 会话 ID |

**客户端行为**：
- 用 `answer` 和 `cards` 补全未渲染的内容（兜底）
- 关闭 SSE 连接

---

## 客户端渲染策略

### 核心原则

**文字逐字涌现，卡片在文本之后一次性出现，客户端用 id 自查详情，用 type（如 movie、audio、musicartist）决定跳转。**

### 渲染流程

**阶段一：流式实时渲染**

```
收到 thinking      → 显示 "思考中..." 动画
收到 tool(calling) → 显示 "正在搜索..." 次级状态
收到 tool(done)    → 隐藏 "正在搜索..."
收到 thinking(llm) → 隐藏 "思考中..."，准备接收文字
收到 token         → 拼接到文本区域，实时渲染 markdown
收到 thinking(reason)→ 显示 "生成推荐理由..."
```

**阶段二：卡片渲染**

```
收到 card          → 拿到 id + reason + type
                   → 调 getMediaItemDetail(id) 获取完整数据
                   → 根据 type 决定卡片点击后的跳转目标页
                   → 渲染卡片（海报、名称、评分、推荐理由等）
                   → 卡片按序排列在文本下方
```

**阶段三：结束**

```
收到 session       → 保存 session_id
收到 done          → 用 answer 和 cards 兜底补全
                   → 关闭 SSE 连接
```

### 推荐布局

```
┌─────────────────────────────────────────┐
│ [状态栏] 思考中... / 正在搜索...          │
├─────────────────────────────────────────┤
│                                         │
│  根据您的要求，推荐3部经典科幻电影：       │  ← token 逐字出现
│                                         │
│  1. **肖申克的救赎** — 蒂姆·罗宾斯...     │
│                                         │
├─────────────────────────────────────────┤
│ ┌─────────┐ ┌─────────┐ ┌─────────┐    │
│ │ [海报]  │ │ [海报]  │ │ [海报]  │    │  ← card 推送后，客户端用 id
│ │肖申克的  │ │ 教父    │ │阿甘正传  │    │     查 Jellyfin 获取详情并渲染
│ │救赎     │ │         │ │         │    │
│ │ 8.7⭐  │ │ 8.69⭐  │ │ 8.47⭐  │    │
│ │影史经典 │ │黑帮巅峰 │ │励志传奇 │    │  ← reason 直接显示
│ └─────────┘ └─────────┘ └─────────┘    │
└─────────────────────────────────────────┘
```

### 客户端并发优化

收到多个 `card` 事件时，可以并行调 `getMediaItemDetail(id)`：
- 每张卡片的详情请求互不依赖
- 可以用 `Promise.all` 或类似机制并发请求
- 每拿到一个详情就立即渲染对应卡片，无需等全部完成

---

## 多轮对话

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

## 海报图片

海报 URL 格式：
```
{JELLYFIN_URL}/Items/{id}/Images/Primary
```

- 直接作为 `<img src>` 使用
- 无需额外认证（Jellyfin 默认公开图片）
- 指定尺寸：`/Images/Primary?maxHeight=300&maxWidth=200`

---

## 错误处理

SSE 流中的错误不会以标准 HTTP 错误返回，而是：
- 如果请求参数错误（如 body 解析失败），直接返回 HTTP 400
- 如果流中途出错，`done` 事件中的 `answer` 可能为空或不完整

HTTP 状态码：
- `200`：成功（开始 SSE 流）
- `400`：参数错误
