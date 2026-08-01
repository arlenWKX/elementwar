<template>
  <div class="modal-overlay" @click.self="$emit('close')">
    <div class="modal-content">
      <div class="modal-header">
        <span>{{ isInitial ? '头家出牌' : '接龙' }}</span>
        <button class="close-btn" @click="$emit('close')">×</button>
      </div>
      <div class="modal-body">
        <p class="hint" v-if="game.fieldSubstance">
          场上: {{ game.fieldSubstance }} ({{ game.fieldMol }}mol)
        </p>
        <p class="hint" v-else>选一张物质牌作为初始</p>

        <div class="section">
          <div class="section-label">物质牌</div>
          <div class="card-row">
            <div
              v-for="card in substanceCards"
              :key="card.instance_id"
              class="card-select"
              :class="{
                substance: true,
                selected: sel.substance?.instance_id === card.instance_id,
                highlight: card._canReact,
              }"
              @click="selectSubstance(card)"
            >
              <div class="cs-type">物</div>
              <div class="cs-name">{{ card.display_name || card.name }}</div>
              <div class="cs-meta">{{ card.meta?.mol || 1 }}mol</div>
              <div class="cs-preview" v-if="card._products?.length">
                →{{ card._products[0] }}
              </div>
            </div>
          </div>
        </div>

        <div class="section" v-if="!isInitial && conditionCards.length > 0">
          <div class="section-label">条件牌</div>
          <div class="card-row">
            <div
              v-for="card in conditionCards"
              :key="card.instance_id"
              class="card-select"
              :class="{
                condition: true,
                selected: sel.conditions.some(c => c.instance_id === card.instance_id),
              }"
              @click="toggleCondition(card)"
            >
              <div class="cs-type">条</div>
              <div class="cs-name">{{ card.display_name || card.name }}</div>
            </div>
          </div>
        </div>

        <div class="section" v-if="!isInitial">
          <div class="section-label">连锁</div>
          <div class="chain-options">
            <label><input type="radio" v-model="sel.continueChain" :value="true" /> 继续</label>
            <label><input type="radio" v-model="sel.continueChain" :value="false" /> 停止</label>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-ghost btn-sm" @click="$emit('close')">取消</button>
        <button class="btn btn-green btn-sm" :disabled="!sel.substance" @click="confirm">确认</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, reactive } from 'vue'
import { useGameStore } from '../stores/game'

const emit = defineEmits(['close'])
const game = useGameStore()

const isInitial = computed(() => !game.fieldSubstance)
const substanceCards = computed(() => game.myHand.filter(c => c.type === 'substance'))
const conditionCards = computed(() => game.myHand.filter(c => c.type === 'condition'))

const sel = reactive({
  substance: null,
  conditions: [],
  continueChain: true,
})

async function selectSubstance(card) {
  sel.substance = card
  if (game.fieldSubstance) {
    try {
      const products = await game.listProducts(
        game.fieldSubstance, game.fieldMol, card.name, card.meta?.mol || 1
      )
      card._products = products
      card._canReact = products.length > 0
    } catch { card._products = []; card._canReact = false }
  }
}

function toggleCondition(card) {
  const i = sel.conditions.findIndex(c => c.instance_id === card.instance_id)
  if (i >= 0) sel.conditions.splice(i, 1)
  else sel.conditions.push(card)
}

function confirm() {
  if (!sel.substance) return
  game.sendReact({
    substance_card_id: sel.substance.instance_id,
    condition_card_ids: sel.conditions.map(c => c.instance_id),
    continue_chain: isInitial.value ? false : sel.continueChain,
  })
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
  max-width: 420px; width: 100%; max-height: 85vh;
  display: flex; flex-direction: column; overflow: hidden;
}
.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px; border-bottom: 2px solid var(--fg);
  font-size: 13px; font-weight: 500;
}
.close-btn {
  background: none; border: none; font-size: 18px;
  color: var(--fg-dim); cursor: pointer; padding: 0;
}
.modal-body { padding: 10px 12px; overflow-y: auto; -webkit-overflow-scrolling: touch; }
.hint { font-size: 11px; color: var(--fg-dim); margin-bottom: 10px; }
.section { margin-bottom: 12px; }
.section-label {
  font-size: 10px; color: var(--fg-mute); margin-bottom: 6px;
  text-transform: uppercase; letter-spacing: 1px;
}
.card-row {
  display: flex; gap: 5px; overflow-x: auto; padding-bottom: 4px;
  -webkit-overflow-scrolling: touch;
}
.card-select {
  flex-shrink: 0; width: 62px; min-height: 76px;
  padding: 5px; background: var(--bg);
  border: 2px solid var(--border-strong);
  box-shadow: 2px 2px 0 var(--border-strong);
  cursor: pointer; transition: all 0.08s; touch-action: manipulation;
}
.card-select:active { transform: translate(2px, 2px); box-shadow: 0 0 0; }
.card-select.substance { border-color: var(--c-blue-d); box-shadow: 2px 2px 0 var(--c-blue-d); }
.card-select.condition { border-color: var(--c-orange-d); box-shadow: 2px 2px 0 var(--c-orange-d); }
.card-select.selected { border-color: var(--c-green-d); box-shadow: 2px 2px 0 var(--c-green-d); background: #e8f0e8; }
.card-select.highlight { border-color: var(--c-green); box-shadow: 2px 2px 0 var(--c-green); }
.cs-type { font-size: 8px; color: var(--fg-mute); }
.card-select.substance .cs-type { color: var(--c-blue-d); }
.card-select.condition .cs-type { color: var(--c-orange-d); }
.cs-name { font-size: 11px; font-weight: 500; margin: 2px 0; word-break: break-all; }
.cs-meta { font-size: 8px; color: var(--fg-dim); }
.cs-preview { font-size: 8px; color: var(--c-green-d); margin-top: 3px; word-break: break-all; }
.chain-options { display: flex; gap: 12px; font-size: 12px; }
.chain-options label { display: flex; align-items: center; gap: 3px; cursor: pointer; }
.modal-footer {
  display: flex; gap: 8px; justify-content: flex-end;
  padding: 8px 12px; border-top: 2px solid var(--fg);
}
</style>
