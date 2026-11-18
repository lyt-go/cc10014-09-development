<template>
  <section class="page" data-module="ods_layer_detail">
    <header class="page-head">
      <div>
        <h2>贴源表详情：{{ entry['表名称'] || '—' }}</h2>
        <p class="page-desc">按来源归属裁剪入库批次字段结构，比对贴源表结果；越权字段不进入ODS可见字段结构与下游查询。</p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="back-list" type="button" @click="goBack">返回列表</button>
      </div>
    </header>

    <p v-if="pageError" class="error-text" data-testid="detail-error">{{ pageError }}</p>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">可见字段数</span>
        <strong class="stat-value" data-testid="stat-visible">{{ visibleFields.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">越权字段数</span>
        <strong class="stat-value" data-testid="stat-overreach">{{ overreachFields.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">结构状态</span>
        <strong class="stat-value" data-testid="stat-status">{{ structure['status'] || '未生成' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">冲突标记</span>
        <strong class="stat-value" data-testid="stat-conflict">{{ conflict['冲突标记'] || '否' }}</strong>
      </article>
    </div>

    <div class="detail-section">
      <h3>贴源表信息</h3>
      <table class="data-table small">
        <tbody>
          <tr v-for="field in entryFields" :key="field">
            <th>{{ field }}</th>
            <td>{{ entry[field] ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="detail-section">
      <h3>来源归属</h3>
      <table class="data-table small">
        <tbody>
          <tr><th>来源系统</th><td data-testid="attr-source">{{ entry['来源系统'] ?? '—' }}</td></tr>
          <tr><th>权限级别</th><td data-testid="attr-permission">{{ attribution['权限级别'] ?? '未登记' }}</td></tr>
          <tr><th>归属字段清单</th><td data-testid="attr-fields">{{ attribution['归属字段清单'] || '—' }}</td></tr>
          <tr v-if="batch"><th>创建批次来源</th><td>{{ batch['来源系统'] }}（{{ batch['批次编号'] }}）</td></tr>
        </tbody>
      </table>
      <form class="filter-bar" @submit.prevent="submitSwitch">
        <label class="filter-item">
          <span>目标来源归属</span>
          <select v-model="switchSource" data-testid="switch-source">
            <option value="" disabled>选择来源系统</option>
            <option v-for="item in switchCandidates" :key="item['来源系统']" :value="item['来源系统']">
              {{ item['来源系统'] }}（{{ item['权限级别'] }}）
            </option>
          </select>
        </label>
        <label class="filter-item">
          <span>边界模式</span>
          <select v-model="switchBoundary" data-testid="switch-boundary">
            <option v-for="b in boundaries" :key="b" :value="b">{{ b }}</option>
          </select>
        </label>
        <button class="btn primary" data-testid="submit-switch" type="submit">切换来源归属</button>
      </form>
    </div>

    <div class="detail-section">
      <h3>ODS可见字段结构</h3>
      <table class="data-table small" data-testid="structure-table">
        <tbody>
          <tr><th>结构编号</th><td>{{ structure['结构编号'] || '—' }}</td></tr>
          <tr><th>状态</th><td data-testid="structure-status">{{ structure['status'] || '未生成' }}</td></tr>
          <tr><th>边界模式</th><td>{{ structure['边界模式'] || '—' }}</td></tr>
          <tr><th>批次字段清单</th><td>{{ structure['批次字段清单'] || '—' }}</td></tr>
          <tr><th>可见字段清单</th><td data-testid="structure-visible">{{ structure['可见字段清单'] || '—' }}</td></tr>
          <tr><th>越权字段清单</th><td data-testid="structure-overreach">{{ structure['越权字段清单'] || '—' }}</td></tr>
          <tr v-if="structure['迁移提示']"><th>迁移提示</th><td data-testid="structure-hint">{{ structure['迁移提示'] }}</td></tr>
          <tr v-if="structure['来源切换清单']"><th>来源切换清单</th><td data-testid="structure-switchlist">{{ structure['来源切换清单'] }}</td></tr>
        </tbody>
      </table>
      <div class="page-actions">
        <label class="filter-item">
          <span>边界模式</span>
          <select v-model="generateBoundary" data-testid="generate-boundary">
            <option v-for="b in boundaries" :key="b" :value="b">{{ b }}</option>
          </select>
        </label>
        <button class="btn primary" data-testid="generate" type="button" @click="submitGenerate">
          {{ structure['status'] ? '重裁剪可见结构' : '生成可见结构' }}
        </button>
        <button
          v-for="action in structureActions"
          :key="action"
          class="btn"
          type="button"
          :data-testid="`structure-${action}`"
          @click="runStructureAction(action)"
        >
          {{ action }}
        </button>
      </div>
    </div>

    <div v-if="conflict['冲突标记'] === '是'" class="detail-section" data-testid="conflict-section">
      <h3>冲突与处理顺序</h3>
      <table class="data-table small">
        <tbody>
          <tr><th>历史批次归属</th><td>{{ conflict['历史来源'] }}</td></tr>
          <tr><th>当前来源系统</th><td>{{ conflict['当前来源'] }}</td></tr>
          <tr><th>处理顺序</th><td data-testid="conflict-rank">第 {{ conflict['处理顺序'] || '—' }} 位（按创建批次升序）</td></tr>
          <tr><th>优先处理表</th><td>{{ conflict['优先表'] || '—' }}</td></tr>
          <tr><th>冲突表清单</th><td>{{ conflict['冲突表清单'] || '—' }}</td></tr>
        </tbody>
      </table>
    </div>

    <div class="detail-section">
      <h3>字段比对（入库批次字段 × 归属字段 × ODS可见）</h3>
      <table class="data-table" data-testid="compare-table">
        <thead>
          <tr><th>字段名称</th><th>入库批次字段</th><th>归属字段</th><th>ODS可见</th><th>判定</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in compare" :key="item['字段名称']" :data-verdict="item['判定']">
            <td>{{ item['字段名称'] }}</td>
            <td>{{ item['批次字段'] }}</td>
            <td>{{ item['归属字段'] }}</td>
            <td>{{ item['ODS可见'] }}</td>
            <td>{{ item['判定'] }}</td>
          </tr>
          <tr v-if="!compare.length"><td colspan="5" class="empty-state">暂无字段，可先生成可见结构</td></tr>
        </tbody>
      </table>
    </div>

    <div class="detail-section">
      <h3>下游查询预览（按可见字段投影）</h3>
      <table class="data-table" data-testid="data-preview">
        <thead>
          <tr><th v-for="col in dataColumns" :key="col">{{ col }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in dataRows" :key="idx">
            <td v-for="col in dataColumns" :key="col">{{ row[col] ?? '—' }}</td>
          </tr>
          <tr v-if="!dataRows.length"><td :colspan="dataColumns.length || 1" class="empty-state">暂无数据</td></tr>
        </tbody>
      </table>
    </div>

    <p v-if="actionMessage" class="action-message" data-testid="action-message">{{ actionMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { request } from '@/api/client'

type Row = Record<string, any>

const route = useRoute()
const router = useRouter()
const entryId = String(route.params.id)

const entryFields = ["表编号", "来源系统", "表名称", "分区字段", "存储格式", "保留天数", "压缩算法", "创建批次", "字段列表", "status"]
const boundaries = ["严格", "放宽"]
const structureActions = ["提交校验", "标记生效", "标记废弃"]

const entry = ref<Row>({})
const structure = ref<Row>({})
const attribution = ref<Row>({})
const batch = ref<Row | null>(null)
const compare = ref<Row[]>([])
const conflict = ref<Row>({})
const attributions = ref<Row[]>([])
const dataRows = ref<Row[]>([])
const pageError = ref('')
const actionMessage = ref('')
const generateBoundary = ref('严格')
const switchBoundary = ref('严格')
const switchSource = ref('')

function splitFields(text: unknown): string[] {
  return String(text ?? '').split(',').map((s) => s.trim()).filter(Boolean)
}

const visibleFields = computed(() => splitFields(structure.value['可见字段清单']))
const overreachFields = computed(() => splitFields(structure.value['越权字段清单']))
const switchCandidates = computed(() =>
  attributions.value.filter((item) => item['来源系统'] && item['来源系统'] !== entry.value['来源系统']),
)
const dataColumns = computed(() => {
  const cols: string[] = []
  for (const row of dataRows.value) {
    for (const key of Object.keys(row)) {
      if (!cols.includes(key)) cols.push(key)
    }
  }
  return cols
})

function goBack() {
  void router.push('/ods_layer')
}

async function readJson(response: Response): Promise<any> {
  const payload = await response.json()
  if (!response.ok) throw new Error(payload?.detail || `接口返回 ${response.status}`)
  return payload
}

async function reload() {
  pageError.value = ''
  try {
    const [visible, attrs, data] = await Promise.all([
      request(`/api/ods_layer/${entryId}/visible`).then(readJson),
      request('/api/ods_layer/attributions').then(readJson),
      request(`/api/ods_layer/${entryId}/data`).then(readJson),
    ])
    entry.value = visible.entry ?? {}
    structure.value = visible.structure ?? {}
    attribution.value = visible.attribution ?? {}
    batch.value = visible.batch ?? null
    compare.value = visible.compare ?? []
    conflict.value = visible.conflict ?? {}
    attributions.value = attrs.items ?? []
    dataRows.value = (data.items ?? []).slice(0, 5)
  } catch (error) {
    pageError.value = error instanceof Error ? error.message : '贴源表详情读取失败'
  }
}

async function postAction(path: string, body: Record<string, unknown>) {
  actionMessage.value = ''
  try {
    const response = await request(`/api/ods_layer/${entryId}${path}`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = await readJson(response)
    actionMessage.value = payload.message ?? ''
    await reload()
  } catch (error) {
    actionMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function submitGenerate() {
  await postAction('/visible/generate', { boundary: generateBoundary.value })
}

async function submitSwitch() {
  if (!switchSource.value) {
    actionMessage.value = '请选择目标来源归属'
    return
  }
  await postAction('/visible/switch', { source: switchSource.value, boundary: switchBoundary.value })
  switchSource.value = ''
}

async function runStructureAction(action: string) {
  await postAction('/visible/actions', { action })
}

onMounted(reload)
</script>

<style scoped>
.error-text { color: #b91c1c; }
.action-message { color: #1f6feb; font-weight: 600; }
.filter-item select { min-width: 180px; padding: 6px 8px; }
</style>
