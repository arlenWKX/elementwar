<template>
  <div class="game-view">
    <button class="btn btn-ghost btn-sm btn-back" @click="goBack">← 退出</button>

    <!-- 1. 对手状态栏 -->
    <div class="opponent-bar">
      <template v-if="game.opponent">
        <span class="player-name">{{ game.opponent.name }}</span>
        <span class="turn-indicator" v-if="isOpponentTurn">◀ 行动中</span>
        <span class="player-stats">
          <span class="stat">牌{{ game.opponent.deck_count }}</span>
          <span class="stat">手{{ game.opponent.hand_count }}</span>
          <span class="stat">弃{{ game.opponent.discard_count }}</span>
          <span class="stat reward" v-if="game.opponent.reward_points > 0">★{{ game.opponent.reward_points }}</span>
        </span>
      </template>
      <span v-else class="hint">无对手</span>
    </div>

    <!-- 2. 对手手牌（牌背）-->
    <div class="opponent-hand">
      <div v-for="i in (game.opponent?.hand_count || 0)" :key="i" class="card-back"></div>
    </div>

    <!-- 3. 对手弃牌区 -->
    <div class="discard-zone" v-if="game.opponent && game.opponent.discard_count > 0">
      <span class="zone-label">弃{{ game.opponent.discard_count }}</span>
    </div>

    <!-- 4. 中央反应区 -->
    <div class="center-zone">
      <div class="field-display" v-if="game.fieldSubstance">
        <span class="field-name">{{ game.fieldSubstance }}</span>
        <span class="field-mol">{{ game.fieldMol }}mol</span>
      </div>
      <div class="field-empty" v-else>
        <span class="hint">场上无物质</span>
      </div>

      <div class="turn-info">
        回合 {{ game.turnNo }} · 轮次 {{ game.roundNo }}
        <span v-if="game.chainStep > 0"> · 连锁 {{ game.chainStep }}</span>
      </div>

      <!-- 选产物 -->
      <div class="product-select" v-if="game.pendingProducts.length > 0 && game.isMyTurn">
        <div class="product-hint">选择产物:</div>
        <div class="product-options">
          <button
            v-for="p in game.pendingProducts"
            :key="p"
            class="btn btn-orange btn-sm"
            @click="game.sendChooseProduct(p)"
          >{{ p }}</button>
        </div>
      </div>

      <!-- 游戏结束 -->
      <div v-if="game.phase === 'finished'" class="game-over">
        <span v-if="state.winner_id === auth.uid">🎉 你赢了！</span>
        <span v-else>游戏结束</span>
      </div>
    </div>

    <!-- 5. 我方弃牌区 -->
    <div class="discard-zone" v-if="game.me && game.me.discard_count > 0">
      <span class="zone-label">弃{{ game.me.discard_count }}</span>
    </div>

    <!-- 6. 我方手牌 + 按钮 -->
    <div class="my-area">
      <div class="action-bar" v-if="game.phase === 'playing'">
        <template v-if="game.isMyTurn && game.turnPhase !== 'awaiting_product'">
          <button class="btn btn-green btn-sm" @click="showReactModal = true">
            {{ game.fieldSubstance ? '接龙' : '头家出牌' }}
          </button>
          <button
            v-if="hasPlayed"
            class="btn btn-ghost btn-sm"
            @click="game.sendEndAction()"
          >停止连锁</button>
          <button class="btn btn-ghost btn-sm" @click="game.sendEndTurn()">结束回合</button>
          <button
            v-if="game.myReward > 0"
            class="btn btn-orange btn-sm"
            @click="showExchangeModal = true"
          >★{{ game.myReward }}</button>
        </template>
        <template v-else-if="game.isMyTurn && game.turnPhase === 'awaiting_product'">
          <span class="action-hint">↑ 请选择产物</span>
        </template>
        <template v-else-if="game.phase === 'finished'">
          <button class="btn btn-ghost btn-sm" @click="goBack">返回大厅</button>
        </template>
        <template v-else>
          <span class="action-hint">等待对手行动...</span>
        </template>
      </div>

      <div class="my-hand">
        <div
          v-for="card in game.myHand"
          :key="card.instance_id"
          class="card"
          :class="card.type"
          @click="onCardClick(card)"
        >
          <div class="card-type">{{ cardTypeLabel(card.type) }}</div>
          <div class="card-name">{{ card.display_name || card.name }}</div>
          <div class="card-meta" v-if="card.type === 'substance'">
            {{ card.meta?.mol || 1 }}mol
          </div>
          <div class="card-frozen" v-if="card.frozen">冻</div>
        </div>
        <div v-if="game.myHand.length === 0" class="hand-empty">空</div>
      </div>
    </div>

    <!-- 7. 我方状态栏 -->
    <div class="my-bar">
      <span class="turn-indicator" v-if="game.isMyTurn">▶ 我的回合</span>
      <span class="player-name">{{ auth.nickname }}</span>
      <span class="player-stats">
        <span class="stat">牌{{ game.me?.deck_count || 0 }}</span>
        <span class="stat">手{{ game.me?.hand_count || 0 }}</span>
        <span class="stat">弃{{ game.me?.discard_count || 0 }}</span>
        <span class="stat reward" v-if="game.myReward > 0">★{{ game.myReward }}</span>
      </span>
    </div>

    <!-- 事件日志 -->
    <div class="event-log-toggle" @click="showLog = !showLog">{{ showLog ? '×' : '记' }}</div>
    <transition name="fade">
      <div v-if="showLog" class="event-log-panel">
        <div class="event-list">
          <div
            v-for="(ev, i) in game.events.slice(-15).reverse()"
            :key="i"
            class="event-line"
            :class="ev.type"
          >
            <span class="ev-time">{{ ev.time }}</span>
            <span class="ev-msg">{{ ev.msg }}</span>
          </div>
        </div>
      </div>
    </transition>

    <ReactModal v-if="showReactModal" @close="showReactModal = false" />
    <ExchangeModal v-if="showExchangeModal" @close="showExchangeModal = false" />
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useGameStore } from '../stores/game'
import ReactModal from '../components/ReactModal.vue'
import ExchangeModal from '../components/ExchangeModal.vue'

const auth = useAuthStore()
const game = useGameStore()
const showReactModal = ref(false)
const showExchangeModal = ref(false)
const showLog = ref(false)

const state = computed(() => game.state)
const isOpponentTurn = computed(() =>
  game.phase === 'playing' && game.opponent &&
  game.currentPlayerId === game.opponent.player_id
)

// 是否已出过牌（用于显示"停止连锁"按钮）
const hasPlayed = computed(() =>
  game.chainStep > 0 || (state.value.chain_history && state.value.chain_history.length > 0)
)

function cardTypeLabel(t) {
  return { substance: '物', condition: '条', privilege: '特' }[t] || t
}

function onCardClick(card) {
  if (game.isMyTurn && game.turnPhase !== 'awaiting_product' && game.phase === 'playing') {
    showReactModal.value = true
  }
}

function goBack() {
  game.disconnect()
  game.roomId = ''
  game.roomCode = ''
  game.reset()
}
</script>

<style scoped>
.game-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* 状态栏 */
.opponent-bar, .my-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  background: var(--bg-card);
  border-bottom: 1.5px solid var(--fg);
  font-size: 11px;
  min-height: 28px;
}
.my-bar {
  border-top: 1.5px solid var(--fg);
  border-bottom: none;
  padding-bottom: calc(5px + var(--safe-bottom));
}
.opponent-bar { padding-top: calc(5px + var(--safe-top)); }

.player-name { font-weight: 500; }
.player-stats { display: flex; gap: 6px; color: var(--fg-dim); font-size: 10px; margin-left: auto; }
.stat.reward { color: var(--c-orange-d); }
.turn-indicator { color: var(--c-blue-d); font-size: 10px; }
.hint { color: var(--fg-mute); font-size: 11px; }

/* 对手手牌 */
.opponent-hand {
  display: flex;
  justify-content: center;
  gap: 3px;
  padding: 5px 10px;
  min-height: 40px;
}
.card-back {
  width: 26px; height: 38px;
  background: var(--c-blue);
  border: 1.5px solid var(--c-blue-d);
  border-radius: var(--radius);
  box-shadow: 1.5px 1.5px 0 var(--c-blue-d);
}

/* 弃牌区 */
.discard-zone { display: flex; justify-content: center; padding: 1px 10px; }
.zone-label {
  font-size: 9px; color: var(--fg-dim);
  padding: 1px 6px; background: var(--bg-soft);
  border: 1px solid var(--border); border-radius: 2px;
}

/* 中央区 */
.center-zone {
  flex: 1;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  padding: 6px 10px; min-height: 0;
}
.field-display {
  text-align: center;
  padding: 8px 20px;
  background: var(--bg-card);
  border: 1.5px solid var(--c-orange);
  box-shadow: 2px 2px 0 var(--c-orange-d);
  border-radius: var(--radius);
}
.field-name {
  display: block; font-size: 18px; font-weight: 700;
  color: var(--c-orange-d); font-family: 'Courier New', monospace;
}
.field-mol { font-size: 10px; color: var(--fg-dim); }
.field-empty { color: var(--fg-mute); font-size: 11px; padding: 12px; }
.turn-info {
  margin-top: 6px; font-size: 10px; color: var(--fg-dim);
  font-family: 'Courier New', monospace;
}

.product-select { margin-top: 8px; text-align: center; }
.product-hint { font-size: 10px; color: var(--c-orange-d); margin-bottom: 6px; }
.product-options { display: flex; gap: 6px; justify-content: center; flex-wrap: wrap; }

.game-over {
  margin-top: 12px; font-size: 14px; font-weight: 600;
  color: var(--c-green-d);
}

/* 我方区域 */
.my-area { padding: 5px 10px; }
.action-bar {
  display: flex; gap: 5px; align-items: center; justify-content: center;
  margin-bottom: 5px; min-height: 28px; flex-wrap: wrap;
}
.action-hint { font-size: 11px; color: var(--fg-dim); }

.my-hand {
  display: flex; gap: 5px; overflow-x: auto;
  padding: 3px 0; min-height: 66px;
  -webkit-overflow-scrolling: touch;
}
.hand-empty { color: var(--fg-mute); font-size: 11px; padding: 20px; width: 100%; text-align: center; }

/* 卡牌 */
.card {
  flex-shrink: 0;
  width: 48px; height: 62px;
  padding: 4px;
  background: var(--bg-card);
  border: 1.5px solid var(--border-strong);
  border-radius: var(--radius);
  box-shadow: 1.5px 1.5px 0 var(--border-strong);
  display: flex; flex-direction: column;
  cursor: pointer;
  transition: transform 0.08s;
  touch-action: manipulation;
}
.card:active { transform: translate(1.5px, 1.5px); box-shadow: 0 0 0; }
.card.substance { border-color: var(--c-blue-d); box-shadow: 1.5px 1.5px 0 var(--c-blue-d); }
.card.condition { border-color: var(--c-orange-d); box-shadow: 1.5px 1.5px 0 var(--c-orange-d); }
.card.privilege { border-color: var(--c-privilege); box-shadow: 1.5px 1.5px 0 var(--c-privilege); }

.card-type { font-size: 8px; color: var(--fg-mute); }
.card.substance .card-type { color: var(--c-blue-d); }
.card.condition .card-type { color: var(--c-orange-d); }
.card.privilege .card-type { color: var(--c-privilege); }
.card-name { font-size: 10px; font-weight: 500; flex: 1; word-break: break-all; }
.card-meta { font-size: 8px; color: var(--fg-dim); }
.card-frozen {
  font-size: 8px; color: var(--c-blue-d);
  background: var(--bg-soft); padding: 0 2px;
  align-self: flex-start; border-radius: 2px;
}

/* 事件日志 */
.event-log-toggle {
  position: fixed;
  top: calc(45% + var(--safe-top));
  right: 0;
  transform: translateY(-50%);
  padding: 6px 4px;
  background: var(--bg-card);
  border: 1.5px solid var(--fg);
  border-right: none;
  box-shadow: 1.5px 1.5px 0 var(--fg);
  border-radius: var(--radius) 0 0 var(--radius);
  font-size: 9px; color: var(--fg-dim);
  cursor: pointer; z-index: 50;
}
.event-log-panel {
  position: fixed;
  top: 50%; right: 0;
  transform: translateY(-50%);
  width: 180px; max-height: 55vh;
  background: var(--bg-card);
  border: 1.5px solid var(--fg);
  border-right: none;
  box-shadow: 2px 2px 0 var(--fg);
  border-radius: var(--radius) 0 0 var(--radius);
  z-index: 50; overflow: hidden;
}
.event-list { max-height: 55vh; overflow-y: auto; padding: 6px; }
.event-line { font-size: 9px; padding: 1px 0; display: flex; gap: 4px; }
.ev-time { color: var(--fg-mute); flex-shrink: 0; }
.ev-msg { color: var(--fg-dim); }
.event-line.success .ev-msg { color: var(--c-green-d); }
.event-line.error .ev-msg { color: var(--c-orange-d); }
.event-line.warn .ev-msg { color: var(--c-orange-d); }
</style>
