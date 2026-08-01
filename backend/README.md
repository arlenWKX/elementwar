# ElementWar Backend

> 《元素战争：临界反应》化学接龙卡牌游戏 — Python + FastAPI 后端

## 快速开始

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --port 3000
```

启动后：
- Web 客户端：http://localhost:3000/
- API 文档：http://localhost:3000/api/docs
- 健康检查：http://localhost:3000/health

## API 概览

### 认证 `/api/auth`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/register` | 注册（返回 JWT） |
| POST | `/login` | 用 UID 登录 |
| POST | `/refresh` | 刷新 token |
| GET | `/profile` | 查询档案（需 JWT） |

### 房间 `/api/rooms`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `` | 创建房间 |
| POST | `/join` | 加入房间 |
| GET | `/{room_id}` | 查询房间 |
| DELETE | `/{room_id}` | 销毁房间 |

### 游戏玩法 `/api/game`（无需认证，前端用）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/reactions:preview` | 反应预览（不进游戏流程） |
| GET | `/substances` | 物质定义列表 |
| GET | `/rules` | 游戏规则与反应条件 |

**命名说明**：使用 Google API 风格的 `:action` 后缀（如 `reactions:preview`），表示对 `reactions` 资源执行 `preview` 操作。

### 管理 `/api/admin`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/rooms` | 列出所有房间 |
| POST | `/cleanup` | 清理空闲房间 |
| GET | `/cache` | 缓存统计 |

### WebSocket `/socket.io`

事件：`ready` / `react` / `end_turn` / `end_action` / `choose_product` / `exchange` / `extract` / `distill`

## 配置

默认值即可工作，无需 `.env` 文件。

如需自定义，复制 `.env.example` 为 `.env`：

```env
APP_PORT=3000
JWT_SECRET=your-secret-key-here
CORS_ORIGINS=*
```

## 关键设计

### 前后端分离

所有化学反应计算在后端完成。前端（TUI/Web）通过 `/api/game/reactions:preview` 预览反应，不依赖 chemkit。

### 反应失败处理

反应失败时**不自动被动结束**。玩家可换牌重试，或主动结束回合。

### 防作弊

每个玩家的 `state:sync` 只含自己的 `my_hand`。公开快照仅含 `hand_count`。

## 部署

### 生产环境

- 设置 `APP_ENV=production`
- 设置 `JWT_SECRET` 为 ≥32 字符随机字符串
- 设置 `CORS_ORIGINS` 为前端域名

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 3000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]
```

## License

MIT
