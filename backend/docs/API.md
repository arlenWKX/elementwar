# ElementWar Backend API 文档

> 版本：v0.2.0  
> 最后更新：2026-07-26  
> Base URL：`http://localhost:3000`

## 目录

- [概览](#概览)
- [认证机制](#认证机制)
- [统一响应格式](#统一响应格式)
- [错误码](#错误码)
- [认证接口](#认证接口)
  - [POST /api/auth/register](#post-apiauthregister)
  - [POST /api/auth/login](#post-apiauthlogin)
  - [POST /api/auth/refresh](#post-apiauthrefresh)
  - [GET /api/auth/profile](#get-apiauthprofile)
  - [GET /api/auth/exists](#get-apiauthexists)
  - [POST /api/auth/generate-uid](#post-apiauthgenerate-uid)
- [房间接口](#房间接口)
  - [POST /api/rooms](#post-apirooms)
  - [POST /api/rooms/join](#post-apiroomsjoin)
  - [GET /api/rooms/{room_id}](#get-apiroomsroom_id)
  - [DELETE /api/rooms/{room_id}](#delete-apiroomsroom_id)
- [管理接口](#管理接口)
  - [POST /api/admin/react](#post-apiadminreact)
  - [GET /api/admin/rooms](#get-apiadminrooms)
  - [POST /api/admin/cleanup](#post-apiadmincleanup)
  - [GET /api/admin/cache](#get-apiadmincache)
- [健康检查](#健康检查)
  - [GET /health](#get-health)
  - [GET /health/detailed](#get-healthdetailed)
- [WebSocket 事件](#websocket-事件)
  - [连接](#连接)
  - [客户端 → 服务端](#客户端--服务端)
  - [服务端 → 客户端](#服务端--客户端)
- [数据模型](#数据模型)
- [典型业务流程](#典型业务流程)

---

## 概览

ElementWar Backend 是《元素战争：临界反应》化学接龙卡牌游戏的后端服务，提供：

1. **REST API**：用户注册、JWT 认证、房间管理、管理工具
2. **WebSocket (Socket.IO)**：实时游戏通信（接龙、回合管理、状态同步）
3. **chemkit 化学引擎**：判定水溶液反应（酸碱、氧化还原、沉淀、络合等）

### 技术栈

| 组件 | 选型 |
|------|------|
| Web 框架 | FastAPI |
| 异步数据库 | SQLAlchemy 2.0 + aiosqlite |
| 数据库 | SQLite（可平滑切换 PostgreSQL/MySQL） |
| 实时通信 | python-socketio（仅 WebSocket transport） |
| 认证 | JWT (HS256)，access + refresh token |
| 化学引擎 | chemkit 2.6.0（自带 sympy 依赖） |

### 路径分布

| 路径前缀 | 用途 |
|----------|------|
| `/health` | 健康检查（无需认证） |
| `/api/auth/*` | 认证相关（注册、登录、refresh） |
| `/api/rooms/*` | 房间管理（需 JWT） |
| `/api/admin/*` | 管理接口（暂未做角色校验） |
| `/api/docs` | Swagger UI 交互式文档 |
| `/api/redoc` | ReDoc 文档 |
| `/api/openapi.json` | OpenAPI 3.1 规范 |
| `/socket.io/` | Socket.IO 端点 |

---

## 认证机制

### JWT 双 Token 体系

| Token 类型 | 用途 | TTL | 携带方式 |
|------------|------|-----|----------|
| access_token | 访问 REST API | 1 小时（可配） | `Authorization: Bearer <token>` |
| refresh_token | 换取新的 access_token | 30 天（可配） | 请求体 `{"refresh_token": "..."}` |

### 典型流程

```
1. POST /api/auth/register {nickname} 
   → 返回 {uid, access_token, refresh_token}

2. 客户端把 uid 和 refresh_token 存 localStorage（access_token 可不持久化）

3. 后续访问 REST → 携带 access_token
   Authorization: Bearer <access_token>

4. access_token 过期 → POST /api/auth/refresh
   → 返回新的 access_token（refresh_token 不变）

5. refresh_token 也过期 → 重新走第 1 步
   可携带原 uid 避免重复注册：POST /api/auth/register {nickname, uid}
```

### 配置

环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JWT_SECRET` | `change-me-...` | **生产环境必须替换**为 ≥32 字符的随机字符串 |
| `JWT_ALGORITHM` | `HS256` | 签名算法 |
| `JWT_ACCESS_TTL_MIN` | `60` | access_token 有效期（分钟） |
| `JWT_REFRESH_TTL_DAY` | `30` | refresh_token 有效期（天） |

### 断线重连 token（与 JWT 区分）

| Token | 用途 | TTL | 一次性 |
|-------|------|-----|--------|
| access_token | REST API 鉴权 | 1h | 否 |
| refresh_token | 换取 access_token | 30d | 否 |
| reconnect_token | Socket.IO 断线恢复房间绑定 | 10min | 是 |

`reconnect_token` 在创建/加入房间时返回，仅用于 Socket.IO 重连场景，不能用于 REST 调用。

---

## 统一响应格式

所有 REST 接口统一返回：

```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": { ... }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `ok` | boolean | 是否成功 |
| `code` | int | 业务错误码（0 = 成功） |
| `msg` | string | 描述信息 |
| `data` | any | 实际数据，失败时为 null |

错误时（HTTP 4xx/5xx）：

```json
{
  "detail": "错误描述"
}
```

或（业务错误）：

```json
{
  "ok": false,
  "code": 1001,
  "msg": "UID 无效，请先注册",
  "data": null
}
```

---

## 错误码

| HTTP 状态 | 业务 code | 含义 |
|-----------|-----------|------|
| 200 | 0 | 成功 |
| 201 | 0 | 创建成功 |
| 400 | - | 请求参数错误（如昵称为空） |
| 401 | - | 未提供 token / token 无效 / token 过期 |
| 403 | - | 无权限（如非房内玩家销毁房间） |
| 404 | - | 资源不存在（房间/用户） |
| 500 | - | 服务端错误 |

Socket.IO 错误事件 `error` 的 `code` 字段：

| code | 含义 |
|------|------|
| `invalid_token` | reconnect_token 无效或已过期 |
| `room_gone` | 房间已不存在 |
| `not_in_room` | 玩家未加入房间 |
| `invalid_uid` | UID 无效 |
| `invalid_payload` | 事件 payload 校验失败 |
| `not_your_turn` | 非当前玩家轮次 |
| `pending_choice` | 当前需先选择产物 |
| `card_not_in_hand` | 卡牌不在手牌中 |
| `not_substance_card` | 打出的牌不是物质牌 |
| `not_condition_card` | 打出的牌不是条件牌 |
| `not_privilege_card` | 打出的牌不是特权卡 |
| `duplicate_condition_type` | 同种条件牌打出多张 |
| `missing_effect` | 特权卡需指定使用方式 |
| `no_new_product` | 反应未产生新物质 |
| `not_awaiting_choice` | 当前不在选产物阶段 |
| `invalid_product` | 产物不在候选列表 |
| `consecutive_active_end` | 不能连续两回合主动结束 |
| `insufficient_reward` | 奖励分不足 |
| `not_enough_players` | 玩家数不足 |

---

## 认证接口

### POST /api/auth/register

注册新用户或获取已存在用户。返回 JWT token 对。

**请求体**：
```json
{
  "nickname": "alice",
  "uid": "180306"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `nickname` | string | 是 | 1-32 字符 |
| `uid` | string | 否 | 客户端可预先生成的 UID；若已存在且昵称匹配，视为已注册返回 is_new=false |

**响应**（201）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "uid": "180306",
    "nickname": "alice",
    "is_new": true,
    "access_token": "eyJhbGc...",
    "refresh_token": "eyJhbGc...",
    "token_type": "Bearer"
  }
}
```

**典型流程**：
1. 客户端首次打开网页 → POST `{nickname}` → 拿到 uid + JWT
2. 客户端本地存储 uid 和 refresh_token（localStorage）
3. 后续每次访问 REST → 携带 access_token
4. access_token 过期 → POST `/api/auth/refresh`
5. refresh_token 过期 → 重新走第 1 步（可携带原 uid 避免重复注册）

---

### POST /api/auth/login

用现有 UID 登录，换取新的 JWT token 对。

**请求体**：
```json
{
  "uid": "180306"
}
```

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "uid": "180306",
    "nickname": "alice",
    "access_token": "eyJhbGc...",
    "refresh_token": "eyJhbGc...",
    "token_type": "Bearer"
  }
}
```

**错误**：
- 404: 用户不存在

---

### POST /api/auth/refresh

用 refresh_token 换取新的 access_token。refresh_token 本身不变。

**请求体**：
```json
{
  "refresh_token": "eyJhbGc..."
}
```

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "access_token": "eyJhbGc...",
    "token_type": "Bearer"
  }
}
```

**错误**：
- 401: refresh token 无效或已过期

---

### GET /api/auth/profile

查询当前用户档案。**需 JWT**。

**请求**：
```
GET /api/auth/profile
Authorization: Bearer <access_token>
```

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "uid": "180306",
    "nickname": "alice",
    "created_at": "2026-07-26T06:42:33.569554",
    "last_seen_at": "2026-07-26T06:42:33.576010+00:00",
    "games_played": 0
  }
}
```

**错误**：
- 401: 未提供 token / token 无效 / 用户不存在

---

### GET /api/auth/exists

检查 UID 是否存在（无需认证，便于客户端注册前预检）。

**请求**：
```
GET /api/auth/exists?uid=180306
```

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "uid": "180306",
    "exists": true
  }
}
```

---

### POST /api/auth/generate-uid

仅生成一个 UID（不落库）。客户端可在用户输入昵称前就预先生成 UID。

**请求**：无请求体

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "uid": "529813"
  }
}
```

---

## 房间接口

### POST /api/rooms

创建房间。**需 JWT**。

**请求体**：
```json
{
  "vs_ai": false,
  "ai_players": 1,
  "total_players": 2
}
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `vs_ai` | bool | false | 是否对战 AI |
| `ai_players` | int | 1 | AI 玩家数（vs_ai=true 时生效，0-2） |
| `total_players` | int | 2 | 房间总玩家数（2 或 3） |

**响应**（201）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "room_id": "A3F28735",
    "code": "810761",
    "player_id": "180306",
    "reconnect_token": "3Y_ulH6ktfsS2sm6tV_DrEe36KdHn0dgV0hOp1NKHcE"
  }
}
```

| 字段 | 说明 |
|------|------|
| `room_id` | 房间 ID（8 位 hex） |
| `code` | 6 位匹配码（数字，便于口头传递） |
| `player_id` | 房间内玩家 ID（= uid） |
| `reconnect_token` | 断线重连 token（10 分钟有效，一次性） |

**说明**：若 `vs_ai=true`，AI 玩家在房间创建时自动加入并 ready，不需调用 `/join`。

---

### POST /api/rooms/join

通过匹配码加入房间。**需 JWT**。

**请求体**：
```json
{
  "code": "810761"
}
```

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "room_id": "A3F28735",
    "player_id": "850435",
    "reconnect_token": "4Y7miwv4vxumv4SURzmEhE_WsEjKMSzC3iqaVYEBJ5Rc",
    "opponent_name": "alice"
  }
}
```

**错误**：
- 404: 房间不存在或已满

---

### GET /api/rooms/{room_id}

查询房间信息。**需 JWT**。

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "room_id": "A3F28735",
    "code": "810761",
    "phase": "waiting",
    "players": [
      {
        "player_id": "180306",
        "name": "alice",
        "is_ai": false,
        "online": true,
        "ready": false,
        "deck_count": 0,
        "hand_count": 0,
        "discard_count": 0,
        "reward_points": 0,
        "ended_round": false
      },
      {
        "player_id": "850435",
        "name": "bob",
        "is_ai": false,
        "online": false,
        "ready": false,
        "deck_count": 0,
        "hand_count": 0,
        "discard_count": 0,
        "reward_points": 0,
        "ended_round": false
      }
    ],
    "current_player_id": null,
    "created_at": "2026-07-26T06:42:33.585000+00:00"
  }
}
```

**phase 取值**：
- `waiting`: 等待玩家加入/准备
- `playing`: 游戏进行中
- `finished`: 游戏结束

**错误**：
- 404: 房间不存在

---

### DELETE /api/rooms/{room_id}

销毁房间。**需 JWT**，**仅限房内玩家**。

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "room destroyed",
  "data": null
}
```

**错误**：
- 403: 仅限房内玩家销毁房间
- 404: 房间不存在

---

## 管理接口

> ⚠️ 当前未做角色/权限校验，生产环境应加 admin 角色 JWT 检查。

### POST /api/admin/react

直接调用 chemkit 计算反应（不进入游戏流程）。用于：调试 chemkit、前端"试反应"功能、纠错记录对照。

**请求体**：
```json
{
  "reactants": [
    {"name": "HCl", "mol": 1.0},
    {"name": "NaOH", "mol": 1.0}
  ],
  "conditions": {
    "volume_l": 1.0,
    "temperature_k": 298.15,
    "pressure_kpa": 101.3,
    "heated": false,
    "concentrated": false
  }
}
```

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "reacted": true,
    "degree": "complete",
    "consumed": {"H^+": 1.0, "OH^-": 1.0},
    "produced": {},
    "final": {"Na^+": 1.0, "Cl^-": 1.0, "H_2O": 1.0},
    "pH": 7.0,
    "equation": "H^+ + OH^- → H_2O",
    "annotations": [],
    "override": null,
    "unknown": [],
    "steps": [...],
    "duration_ms": 5.23,
    "cached": false
  }
}
```

| 字段 | 说明 |
|------|------|
| `reacted` | 是否发生真实化学反应（排除单纯电离/溶解） |
| `degree` | 反应程度：complete / incomplete / hardly / none |
| `consumed` | 净消耗的物质及物质的量（mol） |
| `produced` | 净产物（不含溶剂 H_2O） |
| `final` | 反应后体系中所有物种及物质的量 |
| `pH` | 最终 pH 值（无则 null） |
| `equation` | 配平的反应方程式 |
| `steps` | 反应步骤（redox/proton/precip/complex/neutralize 等） |
| `duration_ms` | chemkit 计算耗时 |
| `cached` | 是否命中缓存 |

---

### GET /api/admin/rooms

列出所有房间（概要）。

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "rooms": [
      {
        "room_id": "A3F28735",
        "code": "810761",
        "phase": "waiting",
        "players": [...],
        "current_player_id": null,
        "turn_no": 0,
        "round_no": 0,
        "vs_ai": false
      }
    ],
    "stats": {
      "total_rooms": 1,
      "waiting": 1,
      "playing": 0,
      "finished": 0,
      "total_sids": 0
    }
  }
}
```

---

### POST /api/admin/cleanup

手动触发清理空闲房间（TTL 由 `game_config.room.idle_timeout_sec` 控制，默认 30 分钟）。

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "removed": 3
  }
}
```

---

### GET /api/admin/cache

查看 chemkit 反应缓存统计。

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "memory": {
      "size": 42,
      "capacity": 2048,
      "hits": 156,
      "misses": 42,
      "hit_rate": 0.788
    }
  }
}
```

---

## 健康检查

### GET /health

基础健康检查（无需认证）。

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "status": "ok",
    "version": "0.2.0",
    "uptime_sec": 0.84,
    "chemkit_loaded": false,
    "materials_loaded": true,
    "db_connected": true,
    "active_rooms": 0
  }
}
```

| 字段 | 说明 |
|------|------|
| `status` | `ok` 或 `degraded`（DB 连不上时降级） |
| `chemkit_loaded` | chemkit Tables 是否已加载 |
| `materials_loaded` | materials.json 是否已加载（约 200 种物质） |
| `db_connected` | 数据库连接是否正常 |

---

### GET /health/detailed

详细健康信息（含缓存统计、物质注册表统计）。

**响应**（200）：
```json
{
  "ok": true,
  "code": 0,
  "msg": "ok",
  "data": {
    "version": "0.2.0",
    "env": "development",
    "uptime_sec": 12.34,
    "chemkit": {
      "loaded": true,
      "data_dir": "./chemkit/data",
      "cache": {"size": 5, "capacity": 2048, "hits": 5, "misses": 5, "hit_rate": 0.5}
    },
    "materials": {
      "loaded": true,
      "substances": 201,
      "conditions": 1,
      "privileges": 5
    },
    "rooms": {"total_rooms": 0, "waiting": 0, "playing": 0, "finished": 0, "total_sids": 0}
  }
}
```

---

## WebSocket 事件

### 连接

**端点**：`ws://localhost:3000/socket.io/`  
**Transport**：仅 websocket（无 long-polling 兜底）  
**Path**：`socket.io`（默认）

**连接时 auth 字段**（任选其一）：

```javascript
// 方式 1：断线重连（用 reconnect_token）
const socket = io('http://localhost:3000', {
  path: '/socket.io',
  transports: ['websocket'],
  auth: { token: '<reconnect_token>' }
});

// 方式 2：JWT + room_id（首次连接或正常重连）
const socket = io('http://localhost:3000', {
  path: '/socket.io',
  transports: ['websocket'],
  auth: { uid: '<uid>', room_id: '<room_id>' }
});

// 方式 3：匿名连接（仅注册 sid，等后续手动 join，少用）
const socket = io('http://localhost:3000', {
  path: '/socket.io',
  transports: ['websocket']
});
```

连接成功后服务端立即推送 `state:sync` 事件，包含完整游戏状态。

---

### 客户端 → 服务端

#### `react` - 打牌接龙

打出物质牌 + 可选条件牌 + 可选特权卡。

**payload**：
```json
{
  "substance_card_id": "a1b2c3d4e5f6",
  "condition_card_ids": ["g7h8i9j0k1l2"],
  "privilege_card_id": null,
  "privilege_effect": null,
  "chosen_product": null,
  "continue_chain": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `substance_card_id` | string | 是 | 物质牌 instance_id |
| `condition_card_ids` | string[] | 否 | 条件牌 instance_id 列表（每种类型限 1 张） |
| `privilege_card_id` | string | 否 | 特权卡 instance_id（每次行动限 1 张） |
| `privilege_effect` | string | 否 | 特权卡使用方式：wildcard / enhance_substance / extract / distill |
| `chosen_product` | string | 否 | 多产物时预先指定 |
| `continue_chain` | bool | 否 | 是否继续连锁（默认 true） |

**服务端响应**：`action:react_ok` 或 `action:react_failed` 或 `action:choose_product`

---

#### `end_turn` - 主动结束回合

不出牌，直接结束本回合。不能连续两回合主动结束。

**payload**：无

---

#### `end_action` - 主动结束本次行动

停止连锁（保留已成功的反应结果）。

**payload**：无

---

#### `choose_product` - 选择产物

当反应产生多个新产物时，玩家从中选一个。

**payload**：
```json
{
  "product": "Na^+"
}
```

---

#### `exchange` - 奖励分兑换

```json
{
  "kind": "recycle",
  "target_card_id": "a1b2c3d4e5f6"
}
```

| kind | 代价 | 作用 | target_card_id |
|------|------|------|----------------|
| `recycle` | 1★ | 回收已打出的牌加入手牌 | 是（已打出的牌） |
| `draw` | 1★ | 牌库顶抽 1 张到手牌 | 否 |
| `discard` | 1★ | 弃 1 张手牌到弃牌堆 | 是（手牌） |
| `exchange_privilege` | 2★ | 从游戏牌池取 1 张特权卡 | 否 |

---

#### `ready` - 玩家准备开始

全员 ready 后才开始游戏。AI 玩家在加入时自动 ready。

**payload**：无

---

#### `leave` - 玩家离开房间

**payload**：无

---

#### `extract` - 特权卡：萃取

从个人弃牌堆选 1 张物质牌加入手牌。不可选冻结卡。

**payload**：
```json
{
  "privilege_card_id": "a1b2c3d4e5f6",
  "target_card_id": "g7h8i9j0k1l2"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `privilege_card_id` | string | 是 | 特权卡 instance_id |
| `target_card_id` | string | 是 | 弃牌堆中物质牌的 instance_id |

**服务端响应**：`privilege:extracted` 或 `error`

---

#### `distill` - 特权卡：蒸馏

查看牌库顶 3 张，选 1 张加入手牌，其余放回牌库底。两阶段协议：

**阶段 1（预览）**：`chosen_index` 不传或为 null
```json
{
  "privilege_card_id": "a1b2c3d4e5f6"
}
```
服务端响应 `privilege:distill_preview`，返回牌库顶 3 张。

**阶段 2（选择）**：`chosen_index` 传 0-2
```json
{
  "privilege_card_id": "a1b2c3d4e5f6",
  "chosen_index": 0
}
```
服务端响应 `privilege:distilled`。

---

### 服务端 → 客户端

#### `state:sync` - 全量状态同步

连接成功时和每次状态变更后推送。

```json
{
  "room_id": "A3F28735",
  "phase": "playing",
  "turn_no": 1,
  "round_no": 1,
  "current_player_id": "180306",
  "field_substance": "HCl",
  "field_substance_mol": 1.0,
  "players": [
    {"player_id": "180306", "name": "alice", "is_ai": false, "online": true, "ready": true, "deck_count": 28, "hand_count": 8, "discard_count": 0, "reward_points": 0, "ended_round": false}
  ],
  "winner_id": null,
  "revision": 12,
  "my_hand": [
    {"instance_id": "a1b2c3", "type": "substance", "name": "NaOH", "display_name": "氢氧化钠", "meta": {"mol": 1.0, "form": "molecule", "category": "base_strong"}, "frozen": false}
  ],
  "my_reward_points": 0
}
```

> `my_hand` 仅推送给对应玩家，不在广播中包含。

---

#### `game:started` - 游戏开始

```json
{"turn_no": 1, "round_no": 1, "current_player_id": "180306"}
```

---

#### `game:ended` - 游戏结束

```json
{"winner_id": "180306", "reason": "deck_empty_and_hand_empty"}
```

---

#### `turn:started` - 回合开始

```json
{"turn_no": 1, "round_no": 1, "current_player_id": "180306"}
```

---

#### `turn:ended` - 回合结束

```json
{"turn_no": 1}
```

---

#### `turn:next_player` - 轮次推进

```json
{"player_id": "850435", "round_no": 2}
```

---

#### `action:react_ok` - 反应成功

```json
{
  "step": 1,
  "field_substance": "NaCl",
  "equation": "H^+ + OH^- → H_2O",
  "degree": "complete",
  "products": ["Na^+", "Cl^-"],
  "continue_chain": true
}
```

---

#### `action:react_failed` - 反应失败

```json
{
  "reactants": ["HCl", "NaCl"],
  "annotations": ["no reaction"],
  "unknown": []
}
```

---

#### `action:choose_product` - 等待玩家选产物

```json
{
  "products": ["Na^+", "Cl^-"],
  "reactants": ["HCl", "NaOH"],
  "result": {...完整反应结果...}
}
```

收到后客户端应提示玩家选择一个 product，然后 emit `choose_product`。

---

#### `action:ended` - 行动结束

```json
{"player_id": "180306", "kind": "chain_end", "action_seq": 5}
```

`kind` 取值：`active`（主动）/ `passive`（被动，无有效反应）/ `chain_end`（连锁结束）

---

#### `cards:drawn` - 抽到牌

```json
{
  "cards": [
    {"instance_id": "x9y8z7", "type": "substance", "name": "HCl", "display_name": "盐酸", "meta": {...}, "frozen": false}
  ],
  "source": "low_security"
}
```

`source` 取值：`low_security`（行动低保）/ `reward_draw`（奖励分兑换抽牌）/ `turn_start`（回合开始补充）

---

#### `cards:deck_added` - 牌库加牌

```json
{"card_type": "substance", "source": "turn_start"}
```

---

#### `reward:earned` - 获得奖励分

```json
{"step": 3, "points": 1, "total": 2}
```

---

#### `reward:exchanged` - 奖励分兑换成功

```json
{"kind": "draw", "cost": 1, "remaining": 1}
```

---

#### `player:joined` / `player:left` / `player:reconnected` - 玩家状态变化

```json
{"player_id": "850435", "sid": "abc123", "reason": "leave"}
```

---

#### `player:ready_changed` - 准备状态变化

```json
{
  "ready_player_id": "180306",
  "all_ready": false,
  "players": [...各玩家公开状态...]
}
```

---

#### `privilege:extracted` - 萃取成功

```json
{
  "card": {"instance_id": "...", "type": "substance", "name": "NaCl", ...}
}
```

---

#### `privilege:distill_preview` - 蒸馏预览（阶段 1）

```json
{
  "cards": [{...}, {...}, {...}],
  "indices": [0, 1, 2]
}
```

---

#### `privilege:distilled` - 蒸馏成功（阶段 2）

```json
{
  "card": {"instance_id": "...", "type": "substance", "name": "HCl", ...}
}
```

---

#### `card:enhanced` - 物质牌强化

```json
{"instance_id": "a1b2c3", "name": "HCl", "mol": 12.0}
```

---

#### `card:condition_enhanced` - 条件牌强化

```json
{"condition_card_ids": ["..."], "scopes": ["action"]}
```

---

#### `error` - 错误

```json
{"code": "not_your_turn", "msg": "非你的轮次"}
```

完整错误码见 [错误码](#错误码)。

---

## 数据模型

### Card（卡牌实例）

```json
{
  "instance_id": "a1b2c3d4e5f6",
  "type": "substance",
  "name": "HCl",
  "display_name": "盐酸",
  "meta": {
    "mol": 1.0,
    "form": "molecule",
    "enhanced": false,
    "category": "acid_strong",
    "default_mol": 1.0,
    "enhanced_mol": 12.0
  },
  "frozen": false
}
```

| type | 说明 |
|------|------|
| `substance` | 物质牌（化学式 + mol） |
| `condition` | 条件牌（目前只有"加热"） |
| `privilege` | 特权卡（万能催化剂/强化/萃取/蒸馏） |

### PlayerState（玩家公开状态）

```json
{
  "player_id": "180306",
  "name": "alice",
  "is_ai": false,
  "online": true,
  "ready": true,
  "deck_count": 28,
  "hand_count": 8,
  "discard_count": 0,
  "reward_points": 0,
  "ended_round": false
}
```

### 牌池构成规则

| 牌类型 | 张数公式 |
|--------|----------|
| 物质牌 | `1 + (copies - 1) × (玩家数 - 1)` |
| 条件牌 | `copies_per_player × 玩家数` |
| 特权卡 | `玩家数` |

物质种类约 200 种，2 人模式约 90 张牌库，3 人模式约 120 张。

---

## 典型业务流程

### 流程 1：注册到开始游戏（2 人 PvP）

```
[Alice]                        [Server]                      [Bob]
  |                               |                             |
  | POST /api/auth/register       |                             |
  |   {nickname: "alice"}         |                             |
  |<-- {uid, access, refresh} ----|                             |
  |                               |                             |
  | POST /api/rooms               |                             |
  |   {vs_ai:false,total:2}       |                             |
  |   Authorization: Bearer xxx   |                             |
  |<-- {room_id, code, token} ----|                             |
  |                               |                             |
  |       (告诉 Bob 匹配码 code)  |                             |
  |                               | POST /api/auth/register     |
  |                               |   {nickname: "bob"}         |
  |                               |<-- {uid, access, refresh} --|
  |                               |                             |
  |                               | POST /api/rooms/join        |
  |                               |   {code: "810761"}          |
  |                               |<-- {room_id, token} --------|
  |                               |                             |
  | Socket.IO connect             |                             |
  |   auth: {uid, room_id}        |                             |
  |<-- state:sync ----------------|                             |
  |                               | Socket.IO connect           |
  |                               |   auth: {uid, room_id}      |
  |                               |<-- state:sync --------------|
  |                               |                             |
  | emit ready                    |                             |
  |<-- player:ready_changed ------|                             |
  |                               | emit ready                  |
  |                               |<-- player:ready_changed ----|
  |                               |                             |
  |<-- game:started --------------|-------- game:started ------>|
  |<-- turn:started --------------|-------- turn:started ------>|
  |<-- state:sync ----------------|-------- state:sync -------->|
  |                               |                             |
  | (轮到 Alice)                  |                             |
  | emit react {substance_card}   |                             |
  |<-- action:react_ok -----------|-------- state:sync -------->|
  |                               |                             |
  | (Alice 决定结束)              |                             |
  | emit end_action               |                             |
  |<-- action:ended --------------|-------- action:ended ------>|
  |<-- turn:next_player ----------|-------- turn:next_player -->|
  |                               |                             |
  |                               | (轮到 Bob)                  |
  |                               | emit react ...              |
  |                               | ...                         |
```

### 流程 2：断线重连

```
[Client]                       [Server]
  |                               |
  | (游戏中网络断开)              |
  |                               |
  | Socket.IO connect             |
  |   auth: {token: <reconnect>}  |
  |                               |
  |<-- player:reconnected --------|
  |<-- state:sync (完整状态) -----|
  |                               |
  | (继续游戏)                    |
```

`reconnect_token` 是一次性的，TTL 10 分钟。如果重连失败（已用过或过期），需重新走 REST 创建/加入房间流程。

### 流程 3：人机对战（vs AI）

```
[Alice]                        [Server]
  |                               |
  | POST /api/rooms               |
  |   {vs_ai:true, ai_players:1, |
  |    total_players:2}           |
  |<-- {room_id, code} -----------|
  |                               |
  | Socket.IO connect             |
  |<-- state:sync (1 玩家) -------|
  |                               |
  | emit ready                    |
  |                               | (服务端检测 AI 未连接 → 启动 AIClient)
  |                               | (AIClient 通过 Socket.IO 走与人类相同的接口)
  |<-- player:ready_changed ------|
  |<-- game:started --------------|
  |<-- turn:started --------------|
  |                               |
  | (轮到 Alice)                  |
  | emit react ...                |
  |                               |
  | (轮到 AI)                     |
  |<-- action:react_ok -----------|  (AI 通过 Socket.IO emit react 触发)
  |<-- state:sync ----------------|
  |                               |
  | ...                           |
```

AI 与人类玩家走完全相同的 Socket.IO 接口，便于调试与维护。

---

## 附录：环境变量完整列表

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_ENV` | `development` | 运行环境：development / staging / production |
| `APP_HOST` | `0.0.0.0` | 监听地址 |
| `APP_PORT` | `3000` | 监听端口 |
| `APP_LOG_LEVEL` | `INFO` | 日志级别 |
| `APP_LOG_FORMAT` | `json` | 日志格式：json / text |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/game.db` | 数据库 URL |
| `JWT_SECRET` | `change-me-...` | **生产环境必须替换** |
| `JWT_ALGORITHM` | `HS256` | JWT 签名算法 |
| `JWT_ACCESS_TTL_MIN` | `60` | access_token 有效期（分钟） |
| `JWT_REFRESH_TTL_DAY` | `30` | refresh_token 有效期（天） |
| `CHEMKIT_DATA_DIR` | `./chemkit/data` | chemkit 数据目录 |
| `CHEMKIT_WARMUP_ON_START` | `true` | 启动时是否预热 chemkit |
| `GAME_CONFIG_PATH` | `./app/data/game_config.json` | 游戏配置路径 |
| `MATERIALS_PATH` | `./app/data/materials.json` | 物质定义路径 |
| `CORS_ORIGINS` | `*` | CORS 允许来源 |

详见 `.env.example`。

---

## 附录：交互式文档

启动服务后访问：

- **Swagger UI**：`http://localhost:3000/api/docs`
- **ReDoc**：`http://localhost:3000/api/redoc`
- **OpenAPI 3.1 规范**：`http://localhost:3000/api/openapi.json`

Swagger UI 可直接在浏览器中测试所有 REST 接口（点击右上角 "Authorize" 输入 `Bearer <access_token>` 即可）。

---

## 附录：游戏规则速查表

### 时间结构

| 概念 | 说明 |
|------|------|
| **回合 (turn)** | 包含多个轮次，持续到所有玩家都结束回合为止 |
| **轮次 (round)** | 按当前行动顺序，每个尚未结束回合的玩家依次进行一次行动 |
| **行动 (action)** | 一个玩家在自己的轮次中，从四种选项中选择其一执行 |
| **反应** | 打出一或多张物质牌并结算产物的过程；连锁反应的每一步也是独立的反应 |

### 区域

| 区域 | 说明 |
|------|------|
| 游戏牌池 | 公共牌池。收纳所有使用后的特权卡，每回合结束时回收弃牌堆中的物质牌/条件牌。补充机制唯一来源 |
| 玩家牌库 | 玩家专属抽牌堆。开局 30 张 |
| 玩家手牌 | 玩家当前持握的牌，上限 10 张。行动结束时刻若超上限立即弃至 10。行动中可超过 |
| 玩家弃牌堆 | 本回合内玩家行动中被覆盖的旧场上物质、已使用的条件牌（冻结至回合结束） |
| 场上物质 | 反应堆中央的唯一物质。非牌，可与手牌/牌库中的物质同名共存 |
| 奖励分 | 与玩家绑定的虚拟计数。仅可在自己的行动中使用，回合结束时清零 |

### 卡牌流转（规则§二）

| 卡牌类型 | 流转路径 |
|---------|---------|
| 物质牌 | 手牌 → 场上 → 被新物质覆盖 → 进入打出者的弃牌堆(冻结) → 回合结束时返回游戏牌池 |
| 条件牌 | 手牌 → 与物质牌同时打出 → 效果结算后进入打出者的弃牌堆(冻结) → 回合结束时返回游戏牌池 |
| 特权卡 | 手牌 → 打出使用 → 效果结算后直接进入游戏牌池 |

### 补充机制（规则§三）

| 机制 | 触发时机 | 效果 |
|------|----------|------|
| 回合启动 | 回合开始时 | 每人从游戏牌池抽 1 张加入个人牌库 |
| 行动里程碑 | 本回合累计总行动次数每经过 `人数×3` | 每人从游戏牌池抽 1 张加入个人牌库 |
| 行动低保 | 行动开始时，若手牌 ≤ 2 | 从个人牌库抽牌补充至 8 张（或牌库清空） |

### 回合流程（规则§四）

1. **回合开始**：清除回合结束状态、清空场上物质、回合启动补充、确定行动顺序
2. **轮次循环**：按行动顺序，每个未结束回合的玩家依次行动
3. **回合结束**：奖励分清零、弃牌堆返回游戏牌池、进入下一回合

**行动顺序三层规则**：
- 第一层：上一回合主动结束的玩家，按主动结束先后顺序**倒序**排列
- 第二层：上一回合未主动/被动结束、至少成功行动 1 次的玩家，按最后一次成功行动先后顺序**正序**排列
- 第三层：上一回合被动结束的玩家，按被动结束先后顺序**正序**排列

### 行动选项（规则§五）

| 选项 | 说明 |
|------|------|
| **接龙** | 打出 1 张物质牌与场上物质反应；可附带条件牌、特权卡（每次限 1 张）。可连锁。从第 3 次成功反应起每次 +1★ |
| **主动结束回合** | 不打出任何牌，宣布结束。本回合剩余轮次不能再行动。**不能连续两回合主动结束** |
| **被动结束回合** | 整次行动无法触发任何有效反应时强制结束 |

**接龙首反应特殊规则**：若玩家为回合内首次行动（场上无物质），从手牌选一张作为初始场上物质（不进弃牌堆，直到被覆盖）。

### 特权卡效果（规则§五）

| 效果 | 说明 |
|------|------|
| 万能催化剂 | 本次反应无需满足常规反应条件即可强制触发 |
| 强化物质牌 | 物质牌效果加强（如浓度提升到 enhanced_mol） |
| 强化条件牌 | 条件牌持续时间提升一级（当次反应 → 行动 → 轮次） |
| 萃取 | 对个人弃牌堆使用：选 1 张物质牌加入手牌（不可选冻结卡） |
| 蒸馏 | 对个人牌库使用：查看牌库顶 3 张，选 1 张加入手牌，其余按任意顺序放回牌库底 |

### 奖励分兑换（规则§六，仅自己行动中使用）

| 消耗 | 效果 | 说明 |
|-----|------|------|
| 1 ★ | **回收** | 从已打出的牌中选 1 张加入手牌（不可选特权卡、不可选冻结卡） |
| 1 ★ | **获取** | 从个人牌库顶抽 1 张加入手牌 |
| 1 ★ | **丢弃** | 从手牌中选 1 张弃到弃牌堆（手牌至少保留 1 张） |
| 2 ★ | **兑换特权卡** | 从游戏牌池取 1 张特权卡；牌池无则临时补充一张 phantom，后续第一张进牌池的特权卡销毁 |

### 胜利条件（规则§七）

当玩家同时满足以下两项时，在其打出最后一张手牌并结算完该次反应瞬间立即获胜：

1. 个人牌库中没有任何牌
2. 手牌中仅剩该张被打出的牌，且打出后手牌为 0
