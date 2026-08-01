<template>
  <div class="ready-view">
    <button class="btn btn-ghost btn-sm btn-back" @click="goBack">← 返回</button>

    <!-- 房间信息 -->
    <div class="room-info-card">
      <div class="info-row">
        <span class="info-label">房间号</span>
        <span class="info-value mono">{{ game.roomCode }}</span>
      </div>
      <div class="info-row">
        <span class="info-label">你的昵称</span>
        <span class="info-value">{{ auth.nickname }}</span>
      </div>
    </div>

    <!-- 玩家状态 -->
    <div class="players-status">
      <div
        v-for="p in game.players"
        :key="p.player_id"
        class="player-slot"
        :class="{ me: p.player_id === auth.uid }"
      >
        <div class="slot-ready" :class="p.ready ? 'yes' : 'no'">
          {{ p.ready ? '✓' : '?' }}
        </div>
        <div class="slot-name">{{ p.name }}</div>
        <div v-if="p.player_id === auth.uid" class="slot-tag">我</div>
      </div>
      <!-- 空位 -->
      <div v-if="game.players.length < 2" class="player-slot empty">
        <div class="slot-ready no">?</div>
        <div class="slot-name">等待加入...</div>
      </div>
    </div>

    <!-- 提示 -->
    <p v-if="game.players.length < 2" class="wait-hint blink">等待对手加入...</p>
    <p v-else-if="!allReady" class="wait-hint">等待对手准备...</p>

    <!-- 准备按钮 -->
    <div class="ready-actions">
      <button
        v-if="!me?.ready"
        class="btn btn-green btn-block"
        @click="game.sendReady()"
      >准备</button>
      <div v-else class="ready-done">
        <span class="blink">✓ 已准备</span>
      </div>
    </div>

    <!-- 解散房间 -->
    <button class="btn btn-ghost btn-sm" @click="dissolveRoom">解散房间</button>

    <!-- 事件日志 -->
    <div class="event-log">
      <div
        v-for="(ev, i) in game.events.slice(-6).reverse()"
        :key="i"
        class="event-line"
        :class="ev.type"
      >
        <span class="ev-time">{{ ev.time }}</span>
        <span class="ev-msg">{{ ev.msg }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useGameStore } from '../stores/game'

const auth = useAuthStore()
const game = useGameStore()

const me = computed(() => game.players.find(p => p.player_id === auth.uid))
const allReady = computed(() =>
  game.players.length >= 2 && game.players.every(p => p.ready)
)

async function goBack() {
  game.disconnect()
  game.roomId = ''
  game.roomCode = ''
  game.reset()
}

async function dissolveRoom() {
  if (!game.roomId) return
  try {
    await fetch(`/api/rooms/${game.roomId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${auth.token}` }
    })
  } catch {}
  game.disconnect()
  game.roomId = ''
  game.roomCode = ''
  game.reset()
}
</script>

<style scoped>
.ready-view {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 100%;
  padding: 20px;
  padding-top: calc(8vh + var(--safe-top));
  gap: 20px;
}

.room-info-card {
  width: 100%; max-width: 280px;
  border: 1.5px solid var(--fg);
  box-shadow: 2px 2px 0 var(--fg);
  background: var(--bg-card);
  border-radius: var(--radius);
  padding: 10px 14px;
}
.info-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 12px; }
.info-label { color: var(--fg-dim); }
.info-value { font-weight: 500; }
.mono { font-family: 'Courier New', monospace; letter-spacing: 2px; }

/* 玩家状态 */
.players-status { display: flex; gap: 12px; justify-content: center; }
.player-slot {
  display: flex; flex-direction: column; align-items: center; gap: 6px;
  padding: 12px 16px;
  border: 1.5px solid var(--border-strong);
  box-shadow: 2px 2px 0 var(--border-strong);
  background: var(--bg-card);
  border-radius: var(--radius);
  min-width: 80px; position: relative;
}
.player-slot.me {
  border-color: var(--c-green-d);
  box-shadow: 2px 2px 0 var(--c-green-d);
}
.player-slot.empty { border-style: dashed; opacity: 0.5; }
.slot-ready {
  width: 24px; height: 24px;
  display: flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 700; border-radius: 2px;
}
.slot-ready.yes { background: var(--c-green); color: #fff; }
.slot-ready.no { background: var(--bg-soft); color: var(--fg-mute); }
.slot-name { font-size: 12px; }
.slot-tag {
  position: absolute; top: -6px; right: -6px;
  background: var(--c-green); color: #fff; font-size: 9px;
  padding: 1px 5px; border-radius: 2px;
}

.wait-hint { font-size: 12px; color: var(--fg-dim); }
.blink { animation: blink 1.2s infinite; }

.ready-actions { width: 100%; max-width: 200px; }
.btn-block { width: 100%; text-align: center; }
.ready-done {
  text-align: center; padding: 8px;
  border: 1.5px solid var(--c-green-d);
  box-shadow: 2px 2px 0 var(--c-green-d);
  background: var(--c-green); color: #fff;
  border-radius: var(--radius); font-size: 13px;
}

.event-log {
  width: 100%; max-width: 280px;
  border: 1.5px solid var(--border);
  background: var(--bg-card);
  border-radius: var(--radius);
  padding: 8px; max-height: 100px; overflow-y: auto;
}
.event-line { font-size: 10px; padding: 1px 0; display: flex; gap: 4px; }
.ev-time { color: var(--fg-mute); flex-shrink: 0; }
.ev-msg { color: var(--fg-dim); }
.event-line.success .ev-msg { color: var(--c-green-d); }
.event-line.error .ev-msg { color: var(--c-orange-d); }
</style>
