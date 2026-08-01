import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

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

export const useAuthStore = defineStore('auth', () => {
  const uid = ref(localStorage.getItem('ew_uid') || '')
  const nickname = ref(localStorage.getItem('ew_nickname') || '')
  const token = ref(localStorage.getItem('ew_token') || '')

  const isLoggedIn = computed(() => !!uid.value && !!token.value)

  async function register(name) {
    const r = await api('POST', '/api/auth/register', { nickname: name })
    const d = r.data
    uid.value = d.uid
    nickname.value = d.nickname
    token.value = d.access_token
    localStorage.setItem('ew_uid', d.uid)
    localStorage.setItem('ew_nickname', d.nickname)
    localStorage.setItem('ew_token', d.access_token)
  }

  async function login(id) {
    const r = await api('POST', '/api/auth/login', { uid: id })
    const d = r.data
    uid.value = d.uid
    nickname.value = d.nickname
    token.value = d.access_token
    localStorage.setItem('ew_uid', d.uid)
    localStorage.setItem('ew_nickname', d.nickname)
    localStorage.setItem('ew_token', d.access_token)
  }

  function logout() {
    uid.value = ''
    nickname.value = ''
    token.value = ''
    localStorage.removeItem('ew_uid')
    localStorage.removeItem('ew_nickname')
    localStorage.removeItem('ew_token')
  }

  /** 验证 token 是否仍然有效（服务器重启后数据库清空，旧 token 失效） */
  async function validateToken() {
    if (!token.value) return false
    try {
      await api('GET', '/api/auth/profile', null, token.value)
      return true
    } catch {
      // token 失效，自动登出
      logout()
      return false
    }
  }

  return { uid, nickname, token, isLoggedIn, register, login, logout, validateToken }
})
