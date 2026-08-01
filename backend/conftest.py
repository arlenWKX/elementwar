"""pytest 配置：确保测试在 backend 目录运行，设置测试环境变量。"""

import os
import sys
from pathlib import Path

# 切换工作目录到 backend（确保相对路径正确）
_BACKEND_ROOT = Path(__file__).resolve().parent
os.chdir(_BACKEND_ROOT)

# 添加到 sys.path
sys.path.insert(0, str(_BACKEND_ROOT))

# 设置测试环境变量（强制覆盖，不用 setdefault）
os.environ["JWT_SECRET"] = "test-secret-need-at-least-32-chars-long"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["CHEMKIT_WARMUP_ON_START"] = "false"

# 重置 settings 单例（可能已被其他模块提前加载）
try:
    from app.config import get_settings, _get_game_config
    get_settings.cache_clear()
    _get_game_config.cache_clear()
except ImportError:
    pass
