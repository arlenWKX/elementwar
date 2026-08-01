"""Pydantic 输入输出 schema。

用于：
- FastAPI REST 接口的请求/响应校验
- Socket.IO 事件 payload 校验
- 内部数据传输对象
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# 通用响应包装
# ============================================================
class ApiResponse(BaseModel):
    """统一 API 响应包装。"""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    code: int = 0
    msg: str = "ok"
    data: Any | None = None


# ============================================================
# 认证
# ============================================================
class RegisterRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=32, description="昵称")
    uid: str | None = Field(default=None, description="客户端可预先生成的 UID")


class RegisterResponse(BaseModel):
    uid: str
    nickname: str
    is_new: bool = Field(description="是否为新建用户；False 表示已存在")
    access_token: str = Field(description="JWT access token（1 小时有效）")
    refresh_token: str = Field(description="JWT refresh token（30 天有效）")
    token_type: str = "Bearer"


class LoginRequest(BaseModel):
    """登录（用现有 UID 换取 token）。"""

    uid: str = Field(min_length=4, max_length=16)


class LoginResponse(BaseModel):
    uid: str
    nickname: str
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"


class ProfileResponse(BaseModel):
    uid: str
    nickname: str
    created_at: str | None = None
    last_seen_at: str | None = None
    games_played: int = 0


class ExistsResponse(BaseModel):
    uid: str
    exists: bool


# ============================================================
# 房间相关
# ============================================================
class CreateRoomRequest(BaseModel):
    vs_ai: bool = Field(default=False, description="是否对战 AI")
    ai_players: int = Field(default=1, ge=0, le=2, description="AI 玩家数（vs_ai=true 时生效）")
    total_players: int = Field(default=2, ge=2, le=3, description="房间总玩家数（2 或 3）")


class CreateRoomResponse(BaseModel):
    room_id: str
    code: str = Field(description="6 位匹配码")
    player_id: str = Field(description="房间内玩家 ID（= uid）")
    reconnect_token: str = Field(description="断线重连 token（10 分钟有效，一次性）")


class JoinRoomRequest(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class JoinRoomResponse(BaseModel):
    room_id: str
    player_id: str
    reconnect_token: str
    opponent_name: str | None = None


class RoomInfoResponse(BaseModel):
    room_id: str
    code: str
    phase: Literal["waiting", "playing", "finished"]
    players: list[dict[str, Any]]
    current_player_id: str | None = None
    created_at: datetime


# ============================================================
# chemkit 反应输入输出
# ============================================================
class ReactantInput(BaseModel):
    name: str = Field(description="物质名，如 'HCl', 'NaOH'")
    mol: float = Field(default=1.0, gt=0, description="物质的量 mol")


class ReactionConditions(BaseModel):
    volume_l: float = Field(default=1.0, gt=0, le=10.0)
    temperature_k: float = Field(default=298.15, gt=273.15 - 1, le=373.15 + 1)
    pressure_kpa: float = Field(default=101.3, gt=0)
    heated: bool = False
    concentrated: bool = False


class ReactionRequest(BaseModel):
    """反应计算请求（管理接口用，玩家走 WebSocket）。"""

    reactants: list[ReactantInput] = Field(min_length=1, max_length=10)
    conditions: ReactionConditions = Field(default_factory=ReactionConditions)


# ============================================================
# WebSocket 事件 payload
# ============================================================
class ReactActionPayload(BaseModel):
    """玩家打出接龙动作的 payload。"""

    substance_card_id: str
    condition_card_ids: list[str] = Field(default_factory=list)
    privilege_card_id: str | None = None
    privilege_effect: str | None = None
    chosen_product: str | None = None
    continue_chain: bool = True


class RewardExchangePayload(BaseModel):
    """奖励分兑换。"""

    kind: Literal["recycle", "draw", "discard", "exchange_privilege"]
    target_card_id: str | None = None


class ExtractPayload(BaseModel):
    """特权卡：萃取 payload。"""

    privilege_card_id: str
    target_card_id: str  # 弃牌堆中的物质牌 instance_id


class DistillPayload(BaseModel):
    """特权卡：蒸馏 payload。

    chosen_index 为 None 时返回牌库顶 3 张预览；
    给定 0-2 时执行选择，选中的牌加入手牌。
    """

    privilege_card_id: str
    chosen_index: int | None = None


# ============================================================
# 健康检查
# ============================================================
class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    uptime_sec: float
    chemkit_loaded: bool
    materials_loaded: bool
    db_connected: bool
    active_rooms: int


__all__ = [
    "ApiResponse",
    "RegisterRequest",
    "RegisterResponse",
    "LoginRequest",
    "LoginResponse",
    "RefreshRequest",
    "RefreshResponse",
    "ProfileResponse",
    "ExistsResponse",
    "CreateRoomRequest",
    "CreateRoomResponse",
    "JoinRoomRequest",
    "JoinRoomResponse",
    "RoomInfoResponse",
    "ReactantInput",
    "ReactionConditions",
    "ReactionRequest",
    "ReactActionPayload",
    "RewardExchangePayload",
    "ExtractPayload",
    "DistillPayload",
    "HealthResponse",
]
