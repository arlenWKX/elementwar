# ElementWar

> 《元素战争：临界反应》—— 化学接龙卡牌游戏

## 项目结构

```
elementwar/
├── backend/              # Python + FastAPI 后端（含 chemkit 化学引擎）
│   ├── app/
│   │   ├── api/          # REST API
│   │   │   ├── auth.py   # /api/auth/* 认证
│   │   │   ├── rooms.py  # /api/rooms/* 房间
│   │   │   ├── game.py   # /api/game/* 游戏玩法（反应预览、物质、规则）
│   │   │   ├── admin.py  # /api/admin/* 管理
│   │   │   └── health.py # /health
│   │   ├── connection/   # Socket.IO
│   │   ├── core/         # 游戏逻辑
│   │   ├── chemkit_adapter/
│   │   └── data/
│   ├── chemkit/
│   └── requirements.txt
├── frontend/
│   ├── tui_client.py        # TUI 入口脚本
│   ├── tui_client/          # TUI 包（基于 Textual）
│   │   ├── __init__.py
│   │   ├── __main__.py      # python -m tui_client
│   │   ├── _entry.py        # HumanApp / AiApp / main
│   │   ├── client.py        # 共享客户端逻辑（HTTP + Socket.IO）
│   │   ├── widgets.py       # 共享 Textual 组件
│   │   └── ai_brain.py      # AI 决策引擎
│   ├── requirements.txt     # TUI 依赖（textual + socketio）
│   └── web/                 # Web 客户端（Alpine.js）
│       ├── index.html
│       ├── style.css
│       └── app.js
├── gamerule.md
├── architecture.md
├── CHANGELOG.md
└── README.md
```

## 快速开始

### 1. 启动后端

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --port 3000
```

### 2. 启动 TUI 客户端

```bash
cd frontend
pip install -r requirements.txt
python tui_client.py           # 人类模式（Textual GUI）
python tui_client.py --ai      # AI Bot 模式（自动对战，GUI 展示）
```

### 3. 或用 Web 客户端

浏览器打开 http://localhost:3000/

## v5.0 主要改进

### 前后端分离

- **前端完全不依赖 chemkit/sympy/pydantic**
- 所有化学反应计算通过后端 API 完成

### API 优化

- `/api/helper/*` → `/api/game/*`（更清晰的命名）
- `POST /api/game/reactions:preview` — 反应预览（Google API 风格动作后缀）
- `GET /api/game/substances` — 物质定义
- `GET /api/game/rules` — 游戏规则（合并条件+房间+奖励+胜利）
- 移除冗余的 `/api/admin/react`（已被 `/api/game/reactions:preview` 取代）

### TUI v5.0（基于 Textual）

- **终端 GUI**：使用 [Textual](https://textual.textualize.io/) 框架，提供丰富的终端界面
- **共享架构**：人类与 AI Bot 共用 `GameClient` + `GameScreen`，仅决策逻辑不同
  - `client.py` — HTTP + Socket.IO + 状态管理（共用）
  - `widgets.py` — Textual 组件：游戏板、手牌、玩家列表、事件日志、操作面板（共用）
  - `ai_brain.py` — AI 决策（仅 AI 模式）
  - `_entry.py` — HumanApp / AiApp（仅入口不同）
- **AI 模式 GUI**：与人类模式界面一致，自动决策，用户只观看
- **反应预览高亮**：选物质牌时调用后端 API，可反应的牌高亮显示产物
- **模态对话框**：接龙/兑换/选产物/萃取/蒸馏都有专门 UI

### Web 客户端

- 黑白主题 + 像素科技风
- Alpine.js 轻量响应式
- 移动优先

## 文档

- [游戏规则](gamerule.md)
- [整体架构](architecture.md)
- [变更日志](CHANGELOG.md)
- [后端 API](backend/docs/API.md)
- [后端说明](backend/README.md)
- [前端说明](frontend/README.md)

## License

MIT
