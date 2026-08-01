"""应用配置。

两套配置：
- Settings: 部署相关（端口、数据库 URL、JWT 密钥、CORS、chemkit 数据目录）
  通过环境变量或 .env 文件加载。
- GameConfig: 游戏规则参数（牌数、手牌上限、奖励分兑换代价）
  从 app/data/game_config.json 加载，支持热重载。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ============================================================
# 路径常量
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
APP_DATA_DIR = PROJECT_ROOT / "app" / "data"


# ============================================================
# 部署配置（环境变量驱动）
# ============================================================
class Settings(BaseSettings):
    """部署配置。

    所有字段均可通过环境变量或 .env 文件覆盖。
    环境变量名 = 字段名大写（如 APP_HOST、JWT_SECRET）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
    )

    # --- 运行环境 ---
    env: Literal["development", "staging", "production"] = Field(
        default="development", alias="APP_ENV"
    )
    host: str = Field(default="0.0.0.0", alias="APP_HOST")
    port: int = Field(default=3000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")

    # --- 数据库 ---
    # 默认使用绝对路径，避免 cwd 不一致导致 SQLite 打开失败
    # （原始默认值 "./data/game.db" 在不同 cwd 下行为不同，是启动失败的常见根因）
    database_url: str = Field(
        default_factory=lambda: f"sqlite+aiosqlite:///{(Path(__file__).resolve().parent.parent / 'data' / 'game.db').as_posix()}",
        alias="DATABASE_URL",
    )

    # --- JWT ---
    jwt_secret: str = Field(
        default="change-me-in-production-please-use-a-long-random-string",
        alias="JWT_SECRET",
    )
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_ttl_min: int = Field(default=60, alias="JWT_ACCESS_TTL_MIN")
    jwt_refresh_ttl_day: int = Field(default=30, alias="JWT_REFRESH_TTL_DAY")

    # --- chemkit 引擎 ---
    # 同样使用绝对路径，避免 cwd 不一致导致 chemkit 数据表加载失败
    chemkit_data_dir: str = Field(
        default_factory=lambda: str((Path(__file__).resolve().parent.parent / "chemkit" / "data")),
        alias="CHEMKIT_DATA_DIR",
    )
    chemkit_warmup_on_start: bool = Field(default=True, alias="CHEMKIT_WARMUP_ON_START")

    # --- 配置文件路径 ---
    game_config_path: str = Field(
        default=str(APP_DATA_DIR / "game_config.json"),
        alias="GAME_CONFIG_PATH",
    )
    materials_path: str = Field(
        default=str(APP_DATA_DIR / "materials.json"),
        alias="MATERIALS_PATH",
    )

    # --- CORS ---
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")

    @field_validator("jwt_secret")
    @classmethod
    def _warn_default_secret(cls, v: str) -> str:
        if v.startswith("change-me"):
            # 仅在 production 警告，dev 不阻塞
            import warnings
            warnings.warn(
                "JWT_SECRET 使用默认值，生产环境必须设置环境变量 JWT_SECRET",
                stacklevel=2,
            )
        return v

    @field_validator("database_url")
    @classmethod
    def _ensure_sqlite_async(cls, v: str) -> str:
        """校验 SQLite URL：转为异步驱动 + 确保父目录存在。"""
        # file:/path → sqlite+aiosqlite:///path
        if v.startswith("file:"):
            v = "sqlite+aiosqlite:///" + v[len("file:"):]
        # sqlite:// → sqlite+aiosqlite://
        if v.startswith("sqlite://") and not v.startswith("sqlite+aiosqlite://"):
            v = v.replace("sqlite://", "sqlite+aiosqlite://", 1)

        # 如果是 SQLite 且不是 :memory:，检查父目录是否存在，不存在则创建
        if "sqlite" in v and ":memory:" not in v:
            # 提取路径部分
            # sqlite+aiosqlite:///path/to/db.sqlite → /path/to/db.sqlite
            # sqlite+aiosqlite:///./data/db.sqlite → ./data/db.sqlite
            prefix = "sqlite+aiosqlite:///"
            if v.startswith(prefix):
                db_path_str = v[len(prefix):]
            else:
                db_path_str = v.split("///")[-1] if "///" in v else ""
            if db_path_str:
                db_path = Path(db_path_str)
                parent = db_path.parent
                if parent and not parent.exists():
                    try:
                        parent.mkdir(parents=True, exist_ok=True)
                    except Exception:
                        # 无法创建目录时，回退到默认绝对路径
                        default_path = Path(__file__).resolve().parent.parent / "data" / "game.db"
                        return f"sqlite+aiosqlite:///{default_path.as_posix()}"
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_dev(self) -> bool:
        return self.env == "development"

    @property
    def is_prod(self) -> bool:
        return self.env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回配置单例。"""
    return Settings()


settings = get_settings()


# ============================================================
# 游戏规则配置（JSON 文件驱动，支持热重载）
# ============================================================
class GameConfig:
    """游戏规则参数。

    与 Settings 区别：
    - Settings: 部署相关（端口、数据库URL、JWT密钥）
    - GameConfig: 玩法相关（牌数、手牌上限、奖励分兑换代价）

    通过 reload_game_config() 热更新，无需重启服务。
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._loaded = False
        self.load()

    def load(self) -> None:
        path = Path(settings.game_config_path)
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as f:
            self._data = json.load(f)
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    # --- 反应体系 ---
    @property
    def default_volume_l(self) -> float:
        return float(self._data.get("reaction", {}).get("default_volume_l", 1.0))

    @property
    def default_temperature_k(self) -> float:
        return float(self._data.get("reaction", {}).get("default_temperature_k", 298.15))

    @property
    def default_pressure_kpa(self) -> float:
        return float(self._data.get("reaction", {}).get("default_pressure_kpa", 101.3))

    @property
    def heating_temperature_k(self) -> float:
        return float(self._data.get("reaction", {}).get("heating_temperature_k", 353.15))

    # --- 房间参数 ---
    @property
    def room_max_players(self) -> int:
        return int(self._data.get("room", {}).get("max_players", 3))

    @property
    def room_min_players(self) -> int:
        return int(self._data.get("room", {}).get("min_players", 2))

    @property
    def room_code_length(self) -> int:
        return int(self._data.get("room", {}).get("code_length", 6))

    @property
    def room_idle_timeout_sec(self) -> int:
        return int(self._data.get("room", {}).get("idle_timeout_sec", 1800))

    @property
    def hand_size_init(self) -> int:
        return int(self._data.get("room", {}).get("hand_size_init", 8))

    @property
    def deck_size_init(self) -> int:
        return int(self._data.get("room", {}).get("deck_size_init", 30))

    @property
    def hand_limit(self) -> int:
        return int(self._data.get("room", {}).get("hand_limit", 10))

    @property
    def low_security_threshold(self) -> int:
        return int(self._data.get("room", {}).get("low_security_threshold", 2))

    @property
    def low_security_draw_to(self) -> int:
        return int(self._data.get("room", {}).get("low_security_draw_to", 8))

    @property
    def milestone_actions_per_player(self) -> int:
        return int(self._data.get("room", {}).get("milestone_actions_per_player", 3))

    # --- 奖励分 ---
    @property
    def chain_reward_start_step(self) -> int:
        return int(self._data.get("reward", {}).get("chain_reward_start_step", 3))

    @property
    def chain_reward_per_step(self) -> int:
        return int(self._data.get("reward", {}).get("chain_reward_per_step", 1))

    @property
    def exchange_costs(self) -> dict[str, int]:
        return self._data.get("reward", {}).get("exchange_costs", {
            "recycle": 1, "draw": 1, "discard": 1, "exchange_privilege": 2,
        })

    def exchange_cost(self, kind: str) -> int:
        return int(self.exchange_costs.get(kind, 1))

    # --- 胜利条件 ---
    @property
    def require_empty_deck_to_win(self) -> bool:
        return bool(self._data.get("victory", {}).get("require_empty_deck", True))

    @property
    def require_empty_hand_to_win(self) -> bool:
        return bool(self._data.get("victory", {}).get("require_empty_hand", True))

    # --- 认证 ---
    @property
    def uid_length(self) -> int:
        return int(self._data.get("auth", {}).get("uid_length", 6))

    @property
    def uid_alphabet(self) -> str:
        return self._data.get("auth", {}).get("uid_alphabet", "0123456789")

    # --- 重连 token TTL（仅用于断线重连的 short-lived token，与 JWT 区分）---
    @property
    def reconnect_token_ttl_sec(self) -> int:
        return int(self._data.get("reconnect", {}).get("token_ttl_sec", 600))


@lru_cache(maxsize=1)
def _get_game_config() -> GameConfig:
    return GameConfig()


def get_game_config() -> GameConfig:
    """获取游戏配置单例。"""
    return _get_game_config()


def reload_game_config() -> GameConfig:
    """重新加载游戏配置（热更新）。"""
    _get_game_config.cache_clear()
    return _get_game_config()


def ensure_dirs() -> None:
    """启动时确保必要目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
