<template>
  <div class="auth-view">
    <!-- 像素拼出 ELEMENTWAR -->
    <div class="pixel-logo" :style="logoStyle">
      <div v-for="(row, ri) in logoGrid" :key="ri" class="pixel-row">
        <div
          v-for="(cell, ci) in row"
          :key="ci"
          class="px"
          :class="{ on: cell }"
        ></div>
      </div>
    </div>

    <!-- 按钮同一行 -->
    <div class="buttons">
      <button class="btn btn-green" @click="mode = 'register'">注册</button>
      <button class="btn btn-blue" @click="mode = 'login'">登录</button>
    </div>

    <!-- 注册表单 -->
    <div v-if="mode === 'register'" class="form">
      <input v-model="nickname" class="input" placeholder="输入昵称" maxlength="32" @keydown.enter="doRegister" />
      <div class="form-actions">
        <button class="btn btn-ghost btn-sm" @click="mode = 'menu'">返回</button>
        <button class="btn btn-green btn-sm" :disabled="!nickname || loading" @click="doRegister">{{ loading ? '...' : '确认' }}</button>
      </div>
    </div>

    <!-- 登录表单 -->
    <div v-if="mode === 'login'" class="form">
      <input v-model="uidInput" class="input" placeholder="输入 UID" maxlength="16" @keydown.enter="doLogin" />
      <div class="form-actions">
        <button class="btn btn-ghost btn-sm" @click="mode = 'menu'">返回</button>
        <button class="btn btn-blue btn-sm" :disabled="!uidInput || loading" @click="doLogin">{{ loading ? '...' : '确认' }}</button>
      </div>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const mode = ref('menu')
const nickname = ref('')
const uidInput = ref('')
const loading = ref(false)
const error = ref('')

// 每个字母 5宽x7高 像素图（字符串）
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
      for (let c = 0; c < 5; c++) {
        row.push(line[c] === '1' ? 1 : 0)
      }
      row.push(0) // 字母间距
    }
    rows.push(row)
  }
  return rows
})

const logoStyle = computed(() => {
  const cols = logoGrid.value[0].length
  return {
    width: `${cols * PX}px`,
    height: `${7 * PX}px`,
  }
})

async function doRegister() {
  if (!nickname.value || loading.value) return
  loading.value = true; error.value = ''
  try { await auth.register(nickname.value) }
  catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function doLogin() {
  if (!uidInput.value || loading.value) return
  loading.value = true; error.value = ''
  try { await auth.login(uidInput.value) }
  catch (e) { error.value = e.message }
  finally { loading.value = false }
}
</script>

<style scoped>
.auth-view {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 100%;
  padding: 20px;
  padding-top: calc(18vh + var(--safe-top));
  gap: 24px;
}

/* 像素 Logo - 无砖块效果，无边框 */
.pixel-logo {
  position: relative;
  display: block;
  overflow: hidden;
}
.pixel-row {
  display: flex;
  height: v-bind(PX + 'px');
}
.px {
  width: v-bind(PX + 'px');
  height: v-bind(PX + 'px');
  flex-shrink: 0;
}
.px.on {
  background: var(--c-orange);
}

/* 按钮同一行 */
.buttons {
  display: flex;
  flex-direction: row;
  gap: 12px;
  align-items: center;
}

.form {
  width: 100%;
  max-width: 260px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.form-actions {
  display: flex;
  gap: 8px;
  justify-content: center;
}
.error {
  color: var(--c-orange-d);
  font-size: 12px;
  text-align: center;
}

@media (max-width: 380px) {
  .px { width: 4px; height: 4px; }
  .pixel-row { height: 4px; }
}
</style>
