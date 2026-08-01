<template>
  <div class="room-view">
    <button class="btn btn-ghost btn-sm btn-back" @click="goBack">← 返回</button>

    <!-- 像素 Logo -->
    <div class="pixel-logo" :style="logoStyle">
      <div v-for="(row, ri) in logoGrid" :key="ri" class="pixel-row">
        <div v-for="(cell, ci) in row" :key="ci" class="px" :class="{ on: cell }"></div>
      </div>
    </div>

    <!-- 个人信息 -->
    <div class="player-info">
      <div class="info-row">
        <span class="info-label">昵称</span>
        <span class="info-value">{{ auth.nickname }}</span>
      </div>
      <div class="info-row">
        <span class="info-label">UID</span>
        <span class="info-value mono">{{ auth.uid }}</span>
      </div>
      <button class="btn btn-ghost btn-sm logout-btn" @click="auth.logout()">退出登录</button>
    </div>

    <!-- 已有房间 -->
    <div v-if="activeRooms.length > 0" class="active-rooms">
      <div class="section-label">进行中的房间</div>
      <div
        v-for="room in activeRooms"
        :key="room.room_id"
        class="room-item"
        @click="resumeRoom(room)"
      >
        <div class="room-item-info">
          <span class="room-code">{{ room.code }}</span>
          <span class="room-phase" :class="room.phase">{{ phaseLabel(room.phase) }}</span>
        </div>
        <div class="room-players">
          <span v-for="p in room.players" :key="p.player_id" class="room-player">
            {{ p.name }}{{ p.ready ? ' ✓' : '' }}
          </span>
        </div>
      </div>
    </div>

    <!-- 房间操作 -->
    <div class="actions" v-if="!game.roomId">
      <button class="btn btn-green btn-block" :disabled="loading" @click="doCreate">
        {{ loading ? '...' : '创建房间' }}
      </button>
      <!-- 加入房间：输入框+按钮无缝连接 -->
      <div class="join-row">
        <input
          v-model="code"
          class="input join-input"
          :class="{ 'has-value': code }"
          placeholder="房间号"
          maxlength="12"
          @keydown.enter="doJoin"
        />
        <button class="btn join-btn" :class="code ? 'btn-blue' : 'btn-ghost'" :disabled="!code || loading" @click="doJoin">
          加入
        </button>
      </div>
    </div>

    <!-- 已创建房间 -->
    <div v-if="game.roomId" class="room-created">
      <p class="hint">房间已创建</p>
      <div class="code-display">{{ game.roomCode }}</div>
      <button class="btn btn-orange btn-sm" @click="dissolveRoom">解散房间</button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useGameStore } from '../stores/game'

const auth = useAuthStore()
const game = useGameStore()
const code = ref('')
const loading = ref(false)
const error = ref('')
const activeRooms = ref([])

const P = {
  E: ["11111","10000","10000","11110","10000","10000","11111"],
  l: ["00001","00001","00001","00001","00001","00001","00001"],
  e: ["00000","01110","10001","11111","10000","01110","00000"],
  m: ["00000","10101","11111","10101","10001","10001","10001"],
  n: ["00000","11001","10101","10011","10001","10001","10001"],
  t: ["10000","11111","10000","10000","10000","10000","10000"],
  W: ["00000","10001","10001","10001","10101","10101","01010"],
  a: ["00000","01110","10001","11111","10001","10001","10001"],
  r: ["00000","11110","10001","10001","11110","10000","10000"],
}
const PX = 5

const logoGrid = computed(() => {
  const text = 'ElementWar'
  const rows = []
  for (let r = 0; r < 7; r++) {
    const row = []
    for (const ch of text) {
      const bits = P[ch] || P.e
      const line = bits[r]
      for (let c = 0; c < 5; c++) row.push(line[c] === '1' ? 1 : 0)
      row.push(0)
    }
    rows.push(row)
  }
  return rows
})
const logoStyle = computed(() => {
  const cols = logoGrid.value[0].length
  return { width: `${cols * PX}px`, height: `${7 * PX}px` }
})

function phaseLabel(p) {
  return { waiting: '准备中', playing: '游戏中', finished: '已结束' }[p] || p
}

function goBack() {
  auth.logout()
}

async function loadActiveRooms() {
  try {
    const resp = await fetch('/api/rooms/my/active', {
      headers: { 'Authorization': `Bearer ${auth.token}` }
    })
    const body = await resp.json()
    if (body.ok) activeRooms.value = body.data.rooms
  } catch {}
}

async function resumeRoom(room) {
  game.roomId = room.room_id
  game.roomCode = room.code
  game.connectSocket()
}

onMounted(() => {
  loadActiveRooms()
})

async function doCreate() {
  loading.value = true; error.value = ''
  try {
    await game.createRoom()
    game.connectSocket()
    await loadActiveRooms()
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function doJoin() {
  if (!code.value || loading.value) return
  loading.value = true; error.value = ''
  try {
    await game.joinRoom(code.value)
    game.connectSocket()
    await loadActiveRooms()
  } catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function dissolveRoom() {
  if (!game.roomId) return
  try {
    await fetch(`/api/rooms/${game.roomId}`, {
      method: 'DELETE',
      headers: { 'Authorization': `Bearer ${auth.token}` }
    })
    game.disconnect()
    game.roomId = ''
    game.roomCode = ''
    await loadActiveRooms()
  } catch (e) {
    error.value = e.message
  }
}
</script>

<style scoped>
.room-view {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 100%;
  padding: 20px;
  padding-top: calc(6vh + var(--safe-top));
  gap: 20px;
}

/* 像素 Logo */
.pixel-logo { display: block; overflow: hidden; }
.pixel-row { display: flex; height: v-bind(PX + 'px'); }
.px { width: v-bind(PX + 'px'); height: v-bind(PX + 'px'); flex-shrink: 0; }
.px.on {
  background: var(--c-orange);
  
  
}

/* 个人信息 */
.player-info {
  width: 100%; max-width: 280px;
  border: 1.5px solid var(--fg);
  box-shadow: 2px 2px 0 var(--fg);
  background: var(--bg-card);
  border-radius: var(--radius);
  padding: 10px 14px;
  position: relative;
}
.logout-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 10px;
  padding: 3px 8px;
}
.info-row { display: flex; justify-content: space-between; padding: 3px 0; font-size: 12px; }
.info-label { color: var(--fg-dim); }
.info-value { font-weight: 500; }
.mono { font-family: 'Courier New', monospace; letter-spacing: 1px; }

/* 已有房间 */
.active-rooms {
  width: 100%; max-width: 280px;
}
.section-label {
  font-size: 10px; color: var(--fg-mute);
  text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px;
}
.room-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 10px; margin-bottom: 6px;
  background: var(--bg-card);
  border: 1.5px solid var(--border-strong);
  border-radius: var(--radius);
  cursor: pointer; transition: border-color 0.1s;
  touch-action: manipulation;
}
.room-item:active { border-color: var(--c-blue); }
.room-item-info { display: flex; gap: 8px; align-items: center; }
.room-code { font-family: 'Courier New', monospace; font-weight: 600; font-size: 13px; }
.room-phase {
  font-size: 9px; padding: 1px 5px; border-radius: 2px;
}
.room-phase.waiting { background: var(--c-orange-l); color: var(--c-orange-d); }
.room-phase.playing { background: var(--c-green); color: #fff; }
.room-phase.finished { background: var(--bg-soft); color: var(--fg-dim); }
.room-players { display: flex; gap: 6px; font-size: 10px; color: var(--fg-dim); }

/* 房间操作 */
.actions {
  width: 100%; max-width: 280px;
  display: flex; flex-direction: column; gap: 10px;
}
.btn-block { width: 100%; text-align: center; }

/* 加入行：无缝连接 */
.join-row {
  display: flex; width: 100%;
  border: 1.5px solid var(--fg);
  border-radius: var(--radius);
  box-shadow: 2px 2px 0 var(--fg);
  overflow: hidden;
}
.join-input {
  flex: 1;
  border: none;
  box-shadow: none;
  border-radius: 0;
}
.join-input:focus { box-shadow: none; border: none; }
.join-btn {
  flex-shrink: 0;
  border: none;
  border-left: 1.5px solid var(--fg);
  border-radius: 0;
  box-shadow: none;
}
.join-btn:active { transform: none; }

/* 已创建房间 */
.room-created { text-align: center; }
.hint { font-size: 11px; color: var(--fg-dim); margin-bottom: 6px; }
.code-display {
  font-family: 'Courier New', monospace;
  font-size: 24px; font-weight: 700; letter-spacing: 5px;
  color: var(--c-orange-d);
  padding: 6px 14px;
  background: var(--bg-card);
  border: 1.5px solid var(--c-orange);
  box-shadow: 2px 2px 0 var(--c-orange-d);
  border-radius: var(--radius);
  margin: 10px 0;
}
.error { color: var(--c-orange-d); font-size: 11px; }
</style>
