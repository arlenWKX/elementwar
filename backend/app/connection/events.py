"""Socket.IO 事件名常量。

集中定义所有客户端↔服务端事件名，避免散落在字符串字面量中导致 typo。

命名约定：
- 客户端 → 服务端: 动词形式 react / end_turn / exchange / choose_product / end_action / ready / leave
- 服务端 → 客户端: xxx:ok / xxx:failed / 状态推送 xxx:sync
"""

from __future__ import annotations


# ============================================================
# 客户端 → 服务端（玩家行动）
# ============================================================
EVT_REACT = "react"                       # 打牌接龙
EVT_END_TURN = "end_turn"                 # 主动结束回合
EVT_EXCHANGE = "exchange"                 # 奖励分兑换
EVT_CHOOSE_PRODUCT = "choose_product"     # 选择产物（多产物时）
EVT_END_ACTION = "end_action"             # 主动结束本次行动（停止连锁）
EVT_READY = "ready"                       # 玩家准备开始
EVT_LEAVE = "leave"                       # 玩家离开房间
EVT_EXTRACT = "extract"                   # 特权卡：萃取
EVT_DISTILL = "distill"                   # 特权卡：蒸馏


# ============================================================
# 服务端 → 客户端（状态推送）
# ============================================================
EVT_STATE_SYNC = "state:sync"                       # 全量状态同步
EVT_GAME_STARTED = "game:started"                   # 游戏开始
EVT_GAME_ENDED = "game:ended"                       # 游戏结束
EVT_TURN_STARTED = "turn:started"                   # 回合开始
EVT_TURN_ENDED = "turn:ended"                       # 回合结束
EVT_TURN_NEXT_PLAYER = "turn:next_player"           # 轮次推进
EVT_ACTION_STARTED = "action:started"               # 行动开始
EVT_ACTION_ENDED = "action:ended"                   # 行动结束
EVT_ACTION_REACT_OK = "action:react_ok"             # 反应成功
EVT_ACTION_REACT_FAILED = "action:react_failed"     # 反应失败
EVT_ACTION_CHOOSE_PRODUCT = "action:choose_product" # 等待玩家选产物
EVT_CARDS_DRAWN = "cards:drawn"                     # 抽到牌
EVT_CARDS_DECK_ADDED = "cards:deck_added"           # 牌库加牌
EVT_REWARD_EARNED = "reward:earned"                 # 获得奖励分
EVT_REWARD_EXCHANGED = "reward:exchanged"           # 奖励分兑换
EVT_PLAYER_LEFT = "player:left"                     # 玩家离开
EVT_PLAYER_RECONNECTED = "player:reconnected"       # 玩家重连
EVT_READY_CHANGED = "player:ready_changed"          # 准备状态变化
EVT_ERROR = "error"                                 # 错误


__all__ = [name for name in dir() if name.startswith("EVT_")]
