import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { io } from 'socket.io-client'
import { useAuthStore } from './auth'

const API_BASE = import.meta.env.DEV ? '' : ''

async function api(method, path, data = null, token = null) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const opts = { method, headers }
  if (data) opts.body = JSON.stringify(data)
  const resp = await fetch(`${API_BASE}${path}`, opts)
  const body = await resp.json()
  if (!resp.ok) throw new Error(body.detail || body.msg || `HTTP ${resp.status}`)
  return body
}

export const useGameStore = defineStore('game', () => {
  const auth = useAuthStore()

  // ===== 连接状态 =====
  const connected = ref(false)
  const socket = ref(null)

  // ===== 房间 =====
  const roomId = ref('')
  const roomCode = ref('')

  // ===== 游戏状态（来自 state:sync）=====
  const state = ref({})
  const myHand = ref([])
  const pendingProducts = ref([])
  const gamePhase = ref('waiting') // waiting | playing | finished
  const gameOver = ref(false)

  // ===== 事件日志 =====
  const events = ref([])

  // ===== 计算属性 =====
  const phase = computed(() => state.value.phase || 'waiting')
  const turnNo = computed(() => state.value.turn_no || 0)
  const roundNo = computed(() => state.value.round_no || 0)
  const chainStep = computed(() => state.value.chain_step || 0)
  const fieldSubstance = computed(() => state.value.field_substance || null)
  const fieldMol = computed(() => state.value.field_substance_mol || 0)
  const currentPlayerId = computed(() => state.value.current_player_id || null)
  const players = computed(() => state.value.players || [])
  const myReward = computed(() => state.value.my_reward_points || 0)
  const turnPhase = computed(() => state.value.turn_phase || 'idle')

  const isMyTurn = computed(() =>
    currentPlayerId.value === auth.uid && phase.value === 'playing'
  )

  const opponent = computed(() =>
    players.value.find(p => p.player_id !== auth.uid)
  )

  const me = computed(() =>
    players.value.find(p => p.player_id === auth.uid)
  )

  // ===== 事件日志辅助 =====
  function log(msg, type = 'info') {
    const now = new Date()
    const time = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`
    events.value.push({ time, msg, type })
    if (events.value.length > 80) events.value = events.value.slice(-80)
  }

  // ===== 房间操作 =====
  async function createRoom() {
    const r = await api('POST', '/api/rooms', {
      vs_ai: false, ai_players: 0, total_players: 2,
    }, auth.token)
    const d = r.data
    roomId.value = d.room_id
    roomCode.value = d.code
    return d
  }

  async function joinRoom(code) {
    const r = await api('POST', '/api/rooms/join', { code }, auth.token)
    const d = r.data
    roomId.value = d.room_id
    roomCode.value = code
    return d
  }

  // ===== Socket.IO 连接 =====
  function connectSocket() {
    if (socket.value?.connected) socket.value.disconnect()

    socket.value = io({
      path: '/socket.io',
      auth: { uid: auth.uid, room_id: roomId.value },
      transports: ['websocket', 'polling'],
    })

    _registerHandlers()
    return socket.value
  }

  function _registerHandlers() {
    const s = socket.value

    s.on('connect', () => {
      connected.value = true
      log('已连接', 'success')
    })

    s.on('disconnect', () => {
      connected.value = false
      log('断开连接', 'error')
    })

    s.on('state:sync', (d) => {
      state.value = d || {}
      myHand.value = d?.my_hand || []
      gamePhase.value = d?.phase || 'waiting'
    })

    s.on('game:started', () => log('游戏开始', 'success'))
    s.on('game:ended', (d) => {
      gameOver.value = true
      const w = d?.winner_id
      const name = players.value.find(p => p.player_id === w)?.name || w
      log(w === auth.uid ? '你赢了！' : `游戏结束 — 胜者: ${name}`, 'success')
    })

    s.on('turn:started', (d) => log(`回合 ${d?.turn_no}`, 'info'))
    s.on('turn:next_player', (d) => {
      const pid = d?.player_id
      const name = players.value.find(p => p.player_id === pid)?.name || pid
      log(pid === auth.uid ? '轮到你' : `轮到 ${name}`, 'info')
    })

    s.on('action:react_ok', (d) => {
      const f = d?.field_substance
      if (d?.initial) log(`初始物质: ${f}`, 'success')
      else log(`反应 → ${f} (step=${d?.step})`, 'success')
    })

    s.on('action:react_failed', (d) => {
      log(`反应失败（可换牌重试）`, 'error')
    })

    s.on('action:choose_product', (d) => {
      pendingProducts.value = d?.products || []
      log(`请选择产物`, 'warn')
    })

    s.on('action:ended', (d) => {
      const labels = { active: '主动结束', passive: '被动结束', chain_end: '连锁结束' }
      log(`行动结束 (${labels[d?.kind] || d?.kind})`, 'info')
    })

    s.on('cards:drawn', (d) => {
      const n = d?.cards?.length || 0
      log(`抽 ${n} 张牌`, 'info')
    })

    s.on('cards:deck_added', () => log('牌库 +1', 'info'))
    s.on('cards:overflow_discarded', (d) => log(`弃 ${d?.cards?.length || 0} 张(超上限)`, 'warn'))
    s.on('reward:earned', (d) => log(`+${d?.points}★ (总${d?.total})`, 'warn'))
    s.on('reward:exchanged', (d) => {
      const labels = { recycle: '回收', draw: '获取', discard: '丢弃', exchange_privilege: '换特权卡' }
      log(`${labels[d?.kind]} -${d?.cost}★`, 'warn')
    })

    s.on('player:ready_changed', (d) => {
      const pid = d?.ready_player_id
      const name = players.value.find(p => p.player_id === pid)?.name || pid
      log(`${name} 准备${d?.all_ready ? ' (全员就绪)' : ''}`, 'info')
    })

    s.on('player:joined', (d) => {
      log(`${d?.name || '玩家'} 加入房间`, 'info')
    })

    s.on('player:left', (d) => log(`${d?.player_id} 离开`, 'info'))
    s.on('privilege:extracted', () => log('萃取成功', 'info'))
    s.on('privilege:distill_preview', (d) => log(`牌库顶 ${d?.cards?.length || 0} 张`, 'info'))
    s.on('privilege:distilled', () => log('蒸馏成功', 'info'))
    s.on('card:enhanced', (d) => log(`强化 ${d?.name}→${d?.mol}mol`, 'info'))

    s.on('error', (d) => log(`[${d?.code}] ${d?.msg}`, 'error'))
  }

  // ===== 游戏操作 =====
  function sendReady() { socket.value?.emit('ready', {}) }

  function sendReact(payload) {
    socket.value?.emit('react', {
      substance_card_id: payload.substance_card_id,
      condition_card_ids: payload.condition_card_ids || [],
      privilege_card_id: payload.privilege_card_id || null,
      privilege_effect: payload.privilege_effect || null,
      chosen_product: payload.chosen_product || null,
      continue_chain: payload.continue_chain ?? true,
    })
  }

  function sendEndTurn() { socket.value?.emit('end_turn', {}) }
  function sendEndAction() { socket.value?.emit('end_action', {}) }
  function sendChooseProduct(p) {
    socket.value?.emit('choose_product', { product: p })
    pendingProducts.value = []
  }
  function sendExchange(kind, targetCardId = null) {
    socket.value?.emit('exchange', { kind, target_card_id: targetCardId })
  }
  function sendExtract(privId, targetId) {
    socket.value?.emit('extract', { privilege_card_id: privId, target_card_id: targetId })
  }
  function sendDistill(privId, chosenIndex = null) {
    socket.value?.emit('distill', { privilege_card_id: privId, chosen_index: chosenIndex })
  }

  // ===== 反应预览 =====
  async function previewReaction(reactants, heated = false) {
    const r = await api('POST', '/api/game/reactions:preview',
      { reactants, heated }, auth.token)
    return r.data
  }

  async function listProducts(fieldName, fieldMol, cardName, cardMol) {
    const r = await api('POST', '/api/game/reactions:preview',
      { reactants: [
        { name: fieldName, mol: fieldMol },
        { name: cardName, mol: cardMol },
      ]}, auth.token)
    return r.data?.products || []
  }

  // ===== 重置 =====
  function reset() {
    state.value = {}
    myHand.value = []
    pendingProducts.value = []
    events.value = []
    gameOver.value = false
    roomId.value = ''
    roomCode.value = ''
  }

  function disconnect() {
    if (socket.value?.connected) socket.value.disconnect()
  }

  return {
    // 状态
    connected, socket, roomId, roomCode,
    state, myHand, pendingProducts, gamePhase, gameOver, events,
    // 计算
    phase, turnNo, roundNo, chainStep, fieldSubstance, fieldMol,
    currentPlayerId, players, myReward, turnPhase,
    isMyTurn, opponent, me,
    // 方法
    log, createRoom, joinRoom, connectSocket,
    sendReady, sendReact, sendEndTurn, sendEndAction,
    sendChooseProduct, sendExchange, sendExtract, sendDistill,
    previewReaction, listProducts, reset, disconnect,
  }
})
