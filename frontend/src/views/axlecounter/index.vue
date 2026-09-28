<template>
  <section class="page" data-module="axlecounter">
    <header class="page-head">
      <div>
        <h2>计轴设备管理</h2>
        <p class="page-desc">维护计轴器，围绕计轴器编号、所属区间、检测磁头、轮轴脉冲做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记计轴器</button>
        <button class="btn" type="button" @click="exportRows">导出计轴设备清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit" :disabled="loadState === 'loading'">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters" :disabled="loadState === 'loading'">重置条件</button>
    </form>

    <!-- 加载中：明确给出等待状态，不与空数据混淆 -->
    <div v-if="loadState === 'loading'" class="state-box" data-state="loading" role="status">
      <span class="spinner" aria-hidden="true"></span>
      <span>正在读取计轴设备数据…</span>
    </div>

    <!-- 查询异常：独立于正常数据的错误态，说明原因并提供重试 -->
    <div v-else-if="loadState === 'error'" class="state-box state-error" data-state="error" role="alert">
      <p class="state-title">计轴器列表读取失败</p>
      <p class="state-desc">{{ errorMessage }}。当前显示的不是最新数据，请检查采集服务或网络后重试。</p>
      <button class="btn primary" type="button" @click="reload">重新查询</button>
    </div>

    <!-- 正常/空数据表格 -->
    <template v-else>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>计轴结论</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)">
            <td v-for="column in columns" :key="column">
              <CellValue :row="row" :column="column" />
            </td>
            <td>
              <span class="status-tag" :data-status="row.status">{{ row['结论'] ?? row.status }}</span>
            </td>
            <td class="row-actions">
              <button
                v-for="action in row['可执行动作'] ?? actions"
                :key="action"
                class="link"
                type="button"
                :disabled="busyKey === `${row.id}:${action}`"
                @click="runAction(action, row)"
              >
                {{ busyKey === `${row.id}:${action}` ? '执行中…' : action }}
              </button>
              <button class="link" type="button" @click="openDetail(row)">查看详情</button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 2" class="empty-state">
              当前筛选条件下暂无计轴设备数据，可调整查询条件或先登记计轴器
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <footer class="page-foot">
      <span>共 {{ total }} 条计轴设备记录</span>
      <span v-if="actionMessage" :class="actionOk ? 'ok-text' : 'error-text'">{{ actionMessage }}</span>
    </footer>

    <!-- 详情抽屉：每次打开都重新拉取，避免显示上一次的旧值；结论与列表同源 -->
    <div v-if="detailOpen" class="drawer-mask" @click.self="closeDetail">
      <aside class="drawer" aria-label="计轴器详情">
        <header class="drawer-head">
          <div>
            <h3>计轴器详情</h3>
            <p v-if="detail" class="page-desc">{{ detail['计轴器编号'] }} · {{ detail['所属区间'] }}</p>
          </div>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </header>

        <div v-if="detailState === 'loading'" class="state-box" role="status">
          <span class="spinner" aria-hidden="true"></span>
          <span>正在读取计轴器明细…</span>
        </div>
        <div v-else-if="detailState === 'error'" class="state-box state-error" role="alert">
          <p class="state-title">计轴器明细读取失败</p>
          <p class="state-desc">{{ detailError }}</p>
          <button class="btn primary" type="button" @click="loadDetail(detailId!)">重新读取</button>
        </div>

        <div v-else-if="detail" class="drawer-body">
          <section class="detail-block">
            <div class="detail-conclusion">
              <span class="status-tag" :data-status="detail.status" style="font-size:14px">
                {{ detail['结论'] }}
              </span>
              <p class="state-desc">{{ detail['结论说明'] }}</p>
            </div>

            <dl class="detail-grid">
              <template v-for="column in detailColumns" :key="column">
                <dt>{{ column }}</dt>
                <dd><CellValue :row="detail" :column="column" :show-missing-detail="true" /></dd>
              </template>
            </dl>
          </section>

          <section class="detail-block">
            <h4>偏差记录</h4>
            <p v-if="!(detail['偏差记录'] || []).length" class="empty-text">暂无偏差记录</p>
            <ul v-else class="record-list">
              <li v-for="(item, idx) in detail['偏差记录']" :key="idx">
                <span class="record-time">{{ item['时间'] }}</span>
                <strong>{{ item['偏差值'] }}</strong>
                <span class="record-desc">{{ item['说明'] }}</span>
              </li>
            </ul>
          </section>

          <section class="detail-block">
            <h4>复位历史</h4>
            <p v-if="!(detail['复位历史'] || []).length" class="empty-text">暂无复位历史</p>
            <ul v-else class="record-list">
              <li v-for="(item, idx) in detail['复位历史']" :key="idx">
                <span class="record-time">{{ item['时间'] }}</span>
                <span class="status-tag" :data-status="item['结果'] === '成功' ? '正常' : '计数偏差'">
                  {{ item['动作'] }}{{ item['结果'] }}
                </span>
                <span class="record-desc">{{ item['说明'] }}</span>
              </li>
            </ul>
          </section>

          <footer class="detail-actions">
            <button
              v-for="action in detail['可执行动作'] ?? actions"
              :key="action"
              class="btn"
              :class="{ primary: action === '校核复位' }"
              type="button"
              :disabled="busyKey === `${detail.id}:${action}`"
              @click="runAction(action, detail)"
            >
              {{ busyKey === `${detail.id}:${action}` ? '执行中…' : action }}
            </button>
          </footer>
        </div>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, ref, type PropType } from 'vue'

import { request } from '@/api/client'

type MissingInfo = { 类型: string; 说明: string }
type HistoryItem = { 时间: string; 动作?: string; 结果?: string; 偏差值?: string; 说明?: string }
interface AxleRow {
  id: number
  status: string
  [key: string]: unknown
  缺失字段?: Record<string, MissingInfo>
  偏差记录?: HistoryItem[]
  复位历史?: HistoryItem[]
  可执行动作?: string[]
  磁头读数陈旧?: boolean
}
type Row = AxleRow

const ENDPOINT = '/api/axlecounter'
// 与后端 LIST_FIELDS 保持一致：磁头读数单列展示，读数时间在详情中给出
const columns = ['计轴器编号', '所属区间', '检测磁头', '轮轴脉冲', '磁头读数', '计数偏差', '复位状态', '校核记录']
const detailColumns = [...columns, '读数时间', '计轴状态']
const actions = ['登记偏差', '校核复位', '办理停用']
const REQUEST_TIMEOUT_MS = 10_000

const rows = ref<Row[]>([])
const total = ref(0)
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

type LoadState = 'loading' | 'ready' | 'error'
const loadState = ref<LoadState>('loading')
const errorMessage = ref('')
const actionMessage = ref('')
const actionOk = ref(true)
const busyKey = ref('')

const stats = computed(() => {
  const count = (status: string) => rows.value.filter((row) => row.status === status).length
  return [
    { label: '正常计轴器', value: count('正常') },
    { label: '偏差计轴器', value: count('计数偏差') },
    { label: '磁头故障', value: count('磁头故障') },
    { label: '数据中断', value: count('数据中断') },
  ]
})

/**
 * 单元格渲染：空值不再统一渲染成“—”，而是按后端给出的缺失类型说明
 * 缺的是哪一项、属于未采集还是漏录；磁头读数还要标出中断前旧值。
 */
const CellValue = defineComponent({
  name: 'CellValue',
  props: {
    row: { type: Object as PropType<Row>, required: true },
    column: { type: String, required: true },
    showMissingDetail: { type: Boolean, default: false },
  },
  setup(props) {
    return () => {
      const row = props.row
      const column = props.column
      const missing = ((row['缺失字段'] as Record<string, MissingInfo> | undefined)?.[column]) ?? null
      const value = row[column]

      if (missing) {
        const title = `${column}暂无：${missing.类型}。${missing.说明}`
        return h('span', { class: `missing-tag ${missing.类型 === '未采集' ? 'miss-uncollected' : 'miss-missing'}`, title }, [
          h('strong', '暂无'),
          h('span', `（${missing.类型}）`),
          props.showMissingDetail ? h('span', { class: 'missing-hint' }, `：${missing.说明}`) : null,
        ])
      }

      const nodes = [h('span', value == null || value === '' ? '—' : String(value))]
      if (column === '磁头读数' && row['磁头读数陈旧']) {
        nodes.push(h('em', { class: 'stale-tag', title: '采集数据中断，该读数为中断前最后一次上报的旧值' }, '中断前旧值'))
      }
      if (column === '计数偏差' && (value == null || value === '')) {
        return h('span', { class: 'muted-text' }, '无偏差')
      }
      return h('span', nodes)
    }
  },
})

function withTimeout(ms: number): { signal: AbortSignal; timer: ReturnType<typeof setTimeout> } {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ms)
  return { signal: controller.signal, timer }
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  actionOk.value = false
  actionMessage.value = '计轴器登记入口尚未接入审批流'
}

async function reload() {
  loadState.value = 'loading'
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  const { signal, timer } = withTimeout(REQUEST_TIMEOUT_MS)
  try {
    const response = await request(`${ENDPOINT}?${query}`, { signal })
    if (!response.ok) {
      throw new Error(await readDetail(response, `服务端返回 ${response.status}`))
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    loadState.value = 'ready'
  } catch (error) {
    // 查询失败时保留旧 rows 但切到错误态：错误与正常数据明确区分，且不会无限转圈
    errorMessage.value = error instanceof Error && error.name === 'AbortError'
      ? '读取超时（10 秒无响应），采集服务可能已中断'
      : error instanceof Error ? error.message : '网络异常，请求未送达'
    loadState.value = 'error'
  } finally {
    clearTimeout(timer)
  }
}

async function readDetail(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json()
    return typeof payload.detail === 'string' ? payload.detail : fallback
  } catch {
    return fallback
  }
}

async function runAction(action: string, row: Row) {
  const id = Number(row.id)
  busyKey.value = `${id}:${action}`
  actionMessage.value = ''
  const { signal, timer } = withTimeout(REQUEST_TIMEOUT_MS)
  try {
    const response = await request(`${ENDPOINT}/${id}/actions`, {
      method: 'POST',
      signal,
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message || '动作未生效，原计轴状态未改变，可重试')
    }
    actionOk.value = true
    actionMessage.value = payload.message
    await reload()
    const currentDetailId = detailId.value
    if (detailOpen.value && currentDetailId !== null && currentDetailId === id) {
      await loadDetail(currentDetailId)
    }
  } catch (error) {
    actionOk.value = false
    actionMessage.value = error instanceof Error && error.name === 'AbortError'
      ? '请求超时，操作结果未知，请刷新列表确认后再重试'
      : error instanceof Error ? error.message : '计轴设备操作失败，可重试'
  } finally {
    busyKey.value = ''
    clearTimeout(timer)
  }
}

// ---- 详情抽屉 -------------------------------------------------------------

const detailOpen = ref(false)
const detailId = ref<number | null>(null)
const detail = ref<Row | null>(null)
const detailState = ref<LoadState>('loading')
const detailError = ref('')

function openDetail(row: Row) {
  detailOpen.value = true
  detail.value = null
  void loadDetail(Number(row.id))
}

function closeDetail() {
  detailOpen.value = false
  detail.value = null
  detailId.value = null
}

async function loadDetail(id: number) {
  detailId.value = id
  detailState.value = 'loading'
  detailError.value = ''
  const { signal, timer } = withTimeout(REQUEST_TIMEOUT_MS)
  try {
    const response = await request(`${ENDPOINT}/${id}`, { signal })
    if (!response.ok) {
      throw new Error(await readDetail(response, `服务端返回 ${response.status}`))
    }
    // 不复用列表里的旧对象：每次都以服务端最新返回为准，磁头读数不会停在上一次
    detail.value = await response.json()
    detailState.value = 'ready'
  } catch (error) {
    detailError.value = error instanceof Error && error.name === 'AbortError'
      ? '读取超时（10 秒无响应），请确认采集链路后重试'
      : error instanceof Error ? error.message : '网络异常，请求未送达'
    detailState.value = 'error'
  } finally {
    clearTimeout(timer)
  }
}

onMounted(reload)
</script>

<style scoped>
.state-box {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: #fff;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 36px 16px;
  color: var(--muted);
  font-size: 14px;
}
.state-error {
  flex-direction: column;
  gap: 6px;
  border-style: solid;
  border-color: #f0b8b0;
  background: #fef6f5;
}
.state-error .state-title { margin: 0; font-size: 15px; font-weight: 600; color: #b42318; }
.state-error .state-desc { margin: 0 0 8px; color: #7a271a; }
.spinner {
  width: 16px; height: 16px;
  border: 2px solid var(--border);
  border-top-color: var(--brand);
  border-radius: 50%;
  animation: axle-spin 0.8s linear infinite;
}
@keyframes axle-spin { to { transform: rotate(360deg); } }

.missing-tag strong { color: #b42318; }
.missing-tag span { color: var(--muted); font-size: 12px; }
.miss-uncollected strong { color: #b54708; }
.missing-hint { display: block; margin-top: 2px; }
.muted-text { color: var(--muted); }
.stale-tag {
  margin-left: 6px;
  font-style: normal;
  font-size: 12px;
  color: #b54708;
  background: #fef6e7;
  border: 1px solid #f4d9a0;
  border-radius: 4px;
  padding: 0 5px;
}
.ok-text { color: #067647; }

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--border);
  background: #f1f5f9;
  color: #475569;
  white-space: nowrap;
}
.status-tag[data-status='正常'] { background: #ecfdf3; border-color: #a6f4c5; color: #067647; }
.status-tag[data-status='计数偏差'] { background: #fef3f2; border-color: #fda29b; color: #b42318; }
.status-tag[data-status='磁头故障'] { background: #fffaeb; border-color: #fedf89; color: #b54708; }
.status-tag[data-status='数据中断'] { background: #f2f4f7; border-color: #d0d5dd; color: #344054; }
.status-tag[data-status='已停用'] { background: #f2f4f7; border-color: #98a2b3; color: #475467; }

.drawer-mask {
  position: fixed; inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex; justify-content: flex-end;
  z-index: 100;
}
.drawer {
  width: 560px; max-width: 92vw;
  height: 100%;
  background: #fff;
  display: flex; flex-direction: column;
  box-shadow: -8px 0 24px rgba(16, 24, 40, 0.12);
}
.drawer-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.drawer-head h3 { margin: 0; font-size: 16px; }
.drawer-body { overflow-y: auto; padding: 16px 20px; }
.detail-block { margin-bottom: 20px; }
.detail-block h4 { margin: 0 0 8px; font-size: 14px; }
.detail-conclusion { margin-bottom: 12px; }
.detail-conclusion .state-desc { margin: 8px 0 0; font-size: 13px; color: var(--muted); }
.detail-grid {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 6px 12px;
  margin: 0;
  font-size: 13px;
}
.detail-grid dt { color: var(--muted); }
.detail-grid dd { margin: 0; }
.record-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.record-list li {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 13px;
  display: flex; flex-direction: column; gap: 2px;
}
.record-time { color: var(--muted); font-size: 12px; }
.record-desc { color: #344054; }
.empty-text { color: var(--muted); font-size: 13px; margin: 4px 0; }
.detail-actions { display: flex; gap: 8px; padding: 12px 0 24px; }
.link:disabled { color: #98a2b3; cursor: not-allowed; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
