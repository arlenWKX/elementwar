<template>
  <div class="app">
    <AuthView v-if="!auth.isLoggedIn" />
    <RoomView v-else-if="!game.roomId" />
    <ReadyView v-else-if="game.phase === 'waiting'" />
    <GameView v-else />
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useAuthStore } from './stores/auth'
import { useGameStore } from './stores/game'
import AuthView from './views/AuthView.vue'
import RoomView from './views/RoomView.vue'
import ReadyView from './views/ReadyView.vue'
import GameView from './views/GameView.vue'

const auth = useAuthStore()
const game = useGameStore()

onMounted(() => {
  // 启动时验证 token，服务器重启后自动登出
  if (auth.isLoggedIn) {
    auth.validateToken()
  }
})
</script>
