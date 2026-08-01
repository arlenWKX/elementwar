# frontend

ElementWar 网页端客户端。

## 技术栈

- **Vue 3** — 响应式 UI 框架
- **Pinia** — 状态管理（auth + game stores）
- **Socket.IO Client** — 实时通信
- **Vite** — 构建工具
- **原生 CSS** — 极简白描淡色系，移动端触控优化

## 开发

```bash
cd frontend/web
npm install
npm run dev    # 开发服务器 http://localhost:5173
```

开发服务器自动代理 `/api` 和 `/socket.io` 到 `http://127.0.0.1:3000`。

## 构建

```bash
npm run build  # 输出到 dist/
```

构建产物可由后端 FastAPI 静态托管，或独立部署。

## 目录结构

```
web/
├── index.html
├── package.json
├── vite.config.js
└── src/
    ├── main.js              # 入口
    ├── App.vue              # 根组件（路由：AuthView / RoomView / GameView）
    ├── assets/
    │   └── main.css         # 全局样式（极简淡色系）
    ├── stores/
    │   ├── auth.js          # 认证状态（注册/登录/JWT）
    │   └── game.js          # 游戏状态（Socket.IO/房间/手牌/事件）
    ├── views/
    │   ├── AuthView.vue     # 登录界面
    │   ├── RoomView.vue     # 房间选择
    │   └── GameView.vue     # 游戏界面
    └── components/
        ├── ReactModal.vue   # 接龙选牌模态
        └── ExchangeModal.vue # 奖励分兑换模态
```

## 游戏界面布局（从上到下）

1. **对手状态栏** — 名字、牌库/手牌/弃牌数、准备状态
2. **对手手牌** — 牌背展示
3. **对手弃牌区** — 弃牌数量
4. **中央反应区** — 场上物质、回合信息、选产物
5. **我方弃牌区** — 弃牌数量
6. **我方手牌 + 按钮** — 手牌横向滚动 + 操作按钮
7. **我方状态栏** — 名字、状态、准备状态

## 设计

- **极简白描**：细线边框、无阴影、大量留白
- **淡色系**：#fafafa 背景、#6b8caf 淡蓝强调、#c4a882 淡黄
- **移动优先**：viewport 适配、safe-area、触控优化（touch-action、active 缩放）
- **卡牌特效预留**：Vue 组件化 + CSS 变量，便于后续添加动画
