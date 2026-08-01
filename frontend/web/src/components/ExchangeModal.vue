<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal-content">
      <div class="modal-header">
        <span>兑换 ★{{ game.myReward }}</span>
        <button class="close-btn" @click="$emit('close')">×</button>
      </div>
      <div class="modal-body">
        <div class="exchange-list">
          <div
            v-for="item in items"
            :key="item.kind"
            class="exchange-item"
            :class="{ disabled: game.myReward < item.cost }"
            @click="doExchange(item)"
          >
            <div>
              <div class="ex-label">{{ item.label }} ★{{ item.cost }}</div>
              <div class="ex-desc">{{ item.desc }}</div>
            </div>
            <div class="ex-afford">{{ game.myReward >= item.cost ? '✓' : '✗' }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useGameStore } from '../stores/game'

const emit = defineEmits(['close'])
const game = useGameStore()

const items = [
  { kind: 'recycle', label: '回收', desc: '从已打出的牌选1张加入手牌', cost: 1 },
  { kind: 'draw', label: '获取', desc: '牌库顶抽1张', cost: 1 },
  { kind: 'discard', label: '丢弃', desc: '手牌弃1张', cost: 1 },
  { kind: 'exchange_privilege', label: '兑换特权卡', desc: '从牌池取1张特权卡', cost: 2 },
]

function doExchange(item) {
  if (game.myReward < item.cost) return
  if (item.kind === 'discard') {
    const hand = game.myHand
    if (hand.length === 0) return
    const idx = prompt(
      `选手牌丢弃 (1-${hand.length}):\n` +
      hand.map((c, i) => `${i+1}. ${c.display_name || c.name}`).join('\n')
    )
    if (!idx) return
    const i = parseInt(idx) - 1
    if (isNaN(i) || i < 0 || i >= hand.length) return
    game.sendExchange('discard', hand[i].instance_id)
  } else if (item.kind === 'recycle') {
    const targetId = prompt('输入已打出牌的 instance_id:')
    if (!targetId) return
    game.sendExchange('recycle', targetId)
  } else {
    game.sendExchange(item.kind)
  }
  emit('close')
}
</script>

<style scoped>
.modal-overlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center;
  z-index: 100; padding: 12px;
}
.modal-content {
  background: var(--bg-card);
  border: 2px solid var(--fg);
  box-shadow: 4px 4px 0 var(--fg);
  max-width: 320px; width: 100%; overflow: hidden;
}
.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-bottom: 2px solid var(--fg);
  font-size: 13px; font-weight: 500;
}
.close-btn {
  background: none; border: none; font-size: 18px;
  color: var(--fg-dim); cursor: pointer;
}
.modal-body { padding: 10px 12px; }
.exchange-list { display: flex; flex-direction: column; gap: 6px; }
.exchange-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 10px; background: var(--bg);
  border: 2px solid var(--border-strong);
  box-shadow: 2px 2px 0 var(--border-strong);
  cursor: pointer; transition: all 0.08s; touch-action: manipulation;
}
.exchange-item:active { transform: translate(2px, 2px); box-shadow: 0 0 0; }
.exchange-item.disabled { opacity: 0.4; }
.ex-label { font-size: 12px; font-weight: 500; }
.ex-desc { font-size: 10px; color: var(--fg-dim); margin-top: 1px; }
.ex-afford { font-size: 13px; color: var(--c-green-d); }
.exchange-item.disabled .ex-afford { color: var(--c-orange-d); }
</style>
