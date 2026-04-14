# SSE 流式对话 — 快速参考

## 端点

| 方法 | 参数方式 | 用途 |
|------|---------|------|
| `POST /ask_stream` | JSON body | 前端 fetch / axios 调用 |
| `GET /ask_stream` | query string | 浏览器原生 `EventSource` |

## 请求

**POST:**
```json
{"question": "推荐3部科幻电影", "session_id": "可选，传入则继续之前的对话"}
```

**GET:**
```
GET /ask_stream?question=推荐3部科幻电影&session_id=可选
```

## 事件流

```
thinking  →  tool(calling)  →  tool(done)  →  thinking  →  token×N  →  thinking  →  card×N  →  session  →  done
```

## 事件一览

| 事件 | 数据 | 说明 |
|------|------|------|
| `thinking` | `{"node": "llm"}` | 思考中，`llm`=LLM生成 / `reason`=生成推荐理由 |
| `tool` | `{"tool":"search_media_json","status":"calling","args":{...}}` | 工具调用开始 |
| `tool` | `{"tool":"search_media_json","status":"done","preview":"..."}` | 工具调用完成 |
| `token` | `{"content": "根"}` | 逐字文本，客户端拼接为完整 markdown |
| `card` | `{"id":"abc123","reason":"推荐理由"}` | 最终推荐卡片（仅 id+reason，客户端自行查详情） |
| `session` | `{"session_id":"a1b2c3","history_count":4}` | 会话信息 |
| `done` | `{"answer":"...","cards":[...],"session_id":"..."}` | 结束信号 |

## 客户端渲染流程

```
1. thinking     → 显示 "思考中..."
2. tool(calling)→ 显示 "正在搜索..."
3. tool(done)   → 隐藏搜索状态
4. thinking     → 准备接收文字
5. token×N      → 拼接并实时渲染 markdown 文本
6. thinking     → 显示 "生成推荐理由..."
7. card×N       → 拿到 id，调 getMediaItemDetail(id) 获取完整数据并渲染卡片
8. session      → 保存 session_id 用于多轮对话
9. done         → 关闭 SSE 连接，用 answer+cards 兜底
```

## 多轮对话

最多 10 轮（20 条消息），100 个并发会话。

```
POST /ask_stream  {"question": "推荐科幻电影"}
→ session: {"session_id": "abc123"}

POST /ask_stream  {"question": "其中哪部最好看", "session_id": "abc123"}
→ 继续对话
```
