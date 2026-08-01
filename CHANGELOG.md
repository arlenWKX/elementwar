# 变更日志

## v5.0 — Textual GUI + API 优化

### TUI v5.0（基于 Textual）

- **终端 GUI**：使用 [Textual](https://textual.textualize.io/) 框架，提供丰富的终端界面
- **共享架构**：人类与 AI Bot 共用 `GameClient` + `GameScreen`
  - `client.py` — HTTP + Socket.IO + 状态管理（共用）
  - `widgets.py` — Textual 组件 + CSS（共用）
  - `ai_brain.py` — AI 决策（仅 AI 模式）
  - `_entry.py` — HumanApp / AiApp（仅入口不同）
- **AI 模式 GUI**：与人类模式界面一致，自动决策，用户只观看
- **模态对话框**：接龙/兑换/选产物/萃取/蒸馏
- **反应预览高亮**：选物质牌时调用后端 API，可反应的牌高亮

### API 优化

- `/api/helper/*` → `/api/game/*`（更清晰的命名）
- `POST /api/game/reactions:preview` — Google API 风格动作后缀
- `GET /api/game/rules` — 合并条件+房间+奖励+胜利
- 移除冗余的 `/api/admin/react`

### 前后端分离验证

- 前端完全不依赖 chemkit/sympy/pydantic
- 所有化学反应计算通过后端 API 完成

## v4.0 — 前后端分离 + TUI/Web 重构

- 前端不再依赖 chemkit
- TUI v4.0 BaseClient 基类
- Web 客户端重构（Alpine.js + 黑白主题）

## v3.0 — 重要修复

- 反应失败可重试
- 产物选择逻辑完善
- SQLite URL 绝对路径

## v2.0 — 初步修复

- SQLite URL 绝对路径
- flush_events 完善

## v1.0 — 初始版本
