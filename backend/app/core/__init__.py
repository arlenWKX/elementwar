"""游戏逻辑层（状态机核心）。

按规则文件实现：
- 状态机：waiting → playing → finished
- 回合/轮次/行动三层时间结构
- 卡牌流转：手牌 → 场上 → 弃牌堆 → 牌池
- 行动处理：接龙 / 主动结束 / 被动结束
- 奖励分结算与兑换
- 胜利判定
"""

from __future__ import annotations
