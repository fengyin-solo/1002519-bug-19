<template>
  <section class="page" data-module="axlecounter">
    <header class="page-head">
      <div>
        <h2>计轴设备管理</h2>
        <p class="page-desc">维护计轴器，围绕计轴器编号、所属区间、检测磁头、轮轴脉冲做登记、筛选与状态流转；缺采集、计数偏差、采集中断等异常会与正常数据区分展示。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出计轴设备清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.cls">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>计轴器编号</span>
        <input v-model="keyword" placeholder="按计轴器编号检索" />
      </label>
      <label class="filter-item">
        <span>计轴状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit" :disabled="loading">查询</button>
      <button class="btn ghost" type="button" :disabled="loading" @click="resetFilters">重置条件</button>
      <button class="btn ghost" type="button" :disabled="loading" @click="reload">刷新</button>
    </form>

    <!-- 查询异常：与“暂无数据”的空态明确区分，并提供重试入口 -->
    <div v-if="loadError" class="notice error" role="alert">
      <div>
        <strong>计轴器列表查询失败</strong>
        <p>{{ loadError }}。以下展示的可能不是最新数据，可重试查询；若持续失败请检查后端采集服务。</p>
      </div>
      <button class="btn" type="button" :disabled="loading" @click="reload">重试查询</button>
    </div>

    <div v-if="loading" class="loading-state" data-testid="list-loading">
      <span class="spinner" aria-hidden="true"></span>
      正在读取计轴器列表…
    </div>

    <template v-else>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in columns" :key="column">{{ column }}</th>
            <th>结论</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-abnormal': row.abnormal, 'row-stale': row.磁头读数状态 === '陈旧', 'row-offline': row.磁头读数状态 === '中断' }">
            <!-- 计轴器编号：点击查看详情，详情结论与本行同源 -->
            <td>
              <button class="link" type="button" @click="openDetail(row)">{{ row.计轴器编号 }}</button>
            </td>
            <td>{{ row.所属区间 }}</td>
            <td>{{ row.检测磁头 }}</td>
            <td>
              <template v-if="!missing(row, '轮轴脉冲')">{{ row.轮轴脉冲 }}</template>
              <span v-else class="cell-missing" :title="hint(row, '轮轴脉冲')">暂无（{{ hint(row, '轮轴脉冲') }}）</span>
            </td>
            <td>
              <template v-if="!missing(row, '磁头读数')">
                <span>{{ row.磁头读数 }}</span>
                <span v-if="row.磁头读数状态 !== '正常'" class="badge" :class="badgeClass(row.磁头读数状态)">{{ row.磁头读数状态 }}</span>
              </template>
              <span v-else class="cell-missing" :title="hint(row, '磁头读数')">暂无（{{ hint(row, '磁头读数') }}）</span>
              <small v-if="row.读数时间" class="cell-sub">采集于 {{ row.读数时间 }}</small>
            </td>
            <td>
              <template v-if="!missing(row, '计数偏差')">
                <span class="deviation-text">{{ row.计数偏差 }}</span>
                <small v-if="lastDeviation(row)" class="cell-sub">{{ lastDeviation(row)?.说明 }}</small>
              </template>
              <span v-else class="cell-ok">暂无偏差</span>
            </td>
            <td>
              {{ row.复位状态 }}
              <small v-if="lastReset(row)?.结果 === '失败'" class="cell-sub error-text">最近一次复位失败：{{ lastReset(row)?.说明 }}</small>
            </td>
            <td>
              <template v-if="!missing(row, '校核记录')">{{ row.校核记录 }}</template>
              <span v-else class="cell-missing" :title="hint(row, '校核记录')">暂无（{{ hint(row, '校核记录') }}）</span>
            </td>
            <td>
              {{ row.计轴状态 }}
              <span v-if="row.磁头读数状态 === '中断'" class="badge offline">采集中断</span>
            </td>
            <td class="conclusion-cell">{{ row.结论 }}</td>
            <td class="row-actions">
              <template v-for="action in availableActions(row)" :key="action">
                <button
                  class="link"
                  type="button"
                  :disabled="busyId === row.id"
                  @click="runAction(action, row)"
                >
                  {{ action }}
                </button>
              </template>
              <!-- 复位失败/被拒时给出重试入口 -->
              <button
                v-if="row.磁头读数状态 !== '正常' && row.status !== '已停用'"
                class="link retry"
                type="button"
                :disabled="busyId === row.id"
                @click="retryReset(row)"
              >
                重试复位
              </button>
              <span class="link scene" @click.prevent>
                <button class="link" type="button" :disabled="busyId === row.id" @click="simulate('interrupt', row)">模拟中断</button>
                <button class="link" type="button" :disabled="busyId === row.id" @click="simulate('stale', row)">模拟陈旧</button>
                <button class="link" type="button" :disabled="busyId === row.id" @click="simulate('recover', row)">恢复链路</button>
              </span>
            </td>
          </tr>
          <!-- 查询成功但确实没有数据：说明空的是“哪一项/当前条件” -->
          <tr v-if="!rows.length">
            <td :colspan="columns.length + 2" class="empty-state">
              暂无符合条件的计轴设备{{ hasFilters ? '（当前筛选条件下未检索到任何计轴器，可重置条件后再查）' : '，可先登记计轴器' }}
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <footer class="page-foot">
      <span v-if="!loading">共 {{ total }} 条计轴设备记录</span>
      <span v-if="actionMessage" :class="actionOk ? 'ok-text' : 'error-text'">{{ actionMessage }}</span>
    </footer>

    <!-- 详情抽屉：直接调 GET 单条接口，字段与结论都由后端同一序列化口径给出 -->
    <div v-if="detail" class="drawer-mask" @click.self="closeDetail">
      <aside class="drawer" role="dialog" aria-label="计轴器详情">
        <header class="drawer-head">
          <div>
            <h3>{{ detail.计轴器编号 }} 详情</h3>
            <p :class="detail.abnormal ? 'error-text' : 'ok-text'">{{ detail.结论 }}</p>
          </div>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </header>

        <div v-if="detailLoading" class="loading-state">
          <span class="spinner" aria-hidden="true"></span>
          正在读取最新详情…
        </div>
        <div v-else-if="detailError" class="notice error">
          <div><strong>详情读取失败</strong><p>{{ detailError }}。列表中的结论可能已过期。</p></div>
          <button class="btn" type="button" @click="loadDetail(detail.id)">重试</button>
        </div>

        <template v-else>
          <table class="detail-table">
            <tbody>
              <tr v-for="field in detailFields" :key="field.name">
                <th>{{ field.label }}</th>
                <td>
                  <template v-if="field.reading && detail.磁头读数状态 !== '正常'">
                    <span :class="missing(detail, field.name) ? 'cell-missing' : ''">
                      {{ missing(detail, field.name) ? `暂无（${hint(detail, field.name)}）` : detail[field.name] }}
                    </span>
                    <span class="badge" :class="badgeClass(detail.磁头读数状态)">{{ detail.磁头读数状态 }}</span>
                  </template>
                  <span v-else-if="missing(detail, field.name)" class="cell-missing">暂无（{{ hint(detail, field.name) }}）</span>
                  <span v-else>{{ detail[field.name] }}</span>
                  <small v-if="field.name === '磁头读数' && detail.读数时间" class="cell-sub">采集于 {{ detail.读数时间 }}</small>
                </td>
              </tr>
            </tbody>
          </table>

          <section class="detail-block">
            <h4>偏差记录（{{ detail.偏差记录.length }} 条在案）</h4>
            <p v-if="!detail.偏差记录.length" class="cell-ok">
              暂无在案偏差<template v-if="detail.偏差记录_已闭环?.length">；历史偏差 {{ detail.偏差记录_已闭环.length }} 条已随复位闭环归档</template>
            </p>
            <ul v-else class="record-list">
              <li v-for="(rec, i) in detail.偏差记录" :key="i">
                <span class="record-time">{{ rec.时间 }}</span>
                <span class="deviation-text">{{ rec.偏差轴数 }} 轴</span>
                <span>{{ rec.说明 }}</span>
              </li>
            </ul>
          </section>

          <section class="detail-block">
            <h4>复位历史（{{ detail.复位历史.length }} 条，只追加不覆盖）</h4>
            <p v-if="!detail.复位历史.length" class="cell-missing">暂无（未执行过复位）</p>
            <ul v-else class="record-list">
              <li v-for="(rec, i) in detail.复位历史" :key="i">
                <span class="record-time">{{ rec.时间 }}</span>
                <span class="badge" :class="rec.结果 === '成功' ? 'ok' : 'fail'">{{ rec.结果 }}</span>
                <span>复位前状态：{{ rec.复位前状态 }}</span>
                <span>{{ rec.说明 }}</span>
              </li>
            </ul>
          </section>

          <footer class="drawer-actions">
            <button class="btn primary" type="button" :disabled="busyId === detail.id || !detail.可复位" @click="retryReset(detail)">
              {{ detail.可复位 ? '校核复位' : '暂不可复位（读数不可用）' }}
            </button>
            <button class="btn" type="button" :disabled="busyId === detail.id" @click="runAction('登记偏差', detail)">登记偏差</button>
          </footer>
        </template>
      </aside>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Scenario = Record<string, unknown>
type Row = Scenario & {
  id: number
  status: string
  abnormal: boolean
  计轴器编号: string
  磁头读数状态: string
  结论: string
  可复位: boolean
  偏差记录: DeviationRecord[]
  偏差记录_已闭环?: DeviationRecord[]
  复位历史: ResetRecord[]
}
type DeviationRecord = { 时间: string; 偏差轴数: string; 说明: string }
type ResetRecord = { 时间: string; 结果: string; 复位前状态: string; 说明: string }

const ENDPOINT = '/api/axlecounter'
const columns = ["计轴器编号", "所属区间", "检测磁头", "轮轴脉冲", "磁头读数", "计数偏差", "复位状态", "校核记录", "计轴状态"]
const statuses = ["正常", "计数偏差", "磁头故障", "已停用"]

const rows = ref<Row[]>([])
const total = ref(0)
const loading = ref(false)
const loadError = ref('')
const keyword = ref('')
const statusFilter = ref('')
const busyId = ref<number | null>(null)
const actionMessage = ref('')
const actionOk = ref(false)

// 详情抽屉
const detail = ref<Row | null>(null)
const detailLoading = ref(false)
const detailError = ref('')

const hasFilters = computed(() => Boolean(keyword.value.trim() || statusFilter.value))

const stats = computed(() => {
  const normal = rows.value.filter((r) => r.status === '正常' && r.磁头读数状态 === '正常').length
  const deviation = rows.value.filter((r) => r.status === '计数偏差').length
  const fault = rows.value.filter((r) => r.status === '磁头故障' || r.磁头读数状态 === '中断').length
  return [
    { label: '正常计轴器', value: normal, cls: '' },
    { label: '偏差计轴器', value: deviation, cls: 'deviation-text' },
    { label: '故障/中断计轴器', value: fault, cls: 'error-text' },
  ]
})

const detailFields = [
  { name: '计轴器编号', label: '计轴器编号' },
  { name: '所属区间', label: '所属区间' },
  { name: '检测磁头', label: '检测磁头' },
  { name: '轮轴脉冲', label: '轮轴脉冲' },
  { name: '磁头读数', label: '磁头读数', reading: true },
  { name: '计数偏差', label: '计数偏差' },
  { name: '复位状态', label: '复位状态' },
  { name: '校核记录', label: '校核记录' },
  { name: '计轴状态', label: '计轴状态' },
]

function missing(row: Row, field: string): boolean {
  return Boolean(row[`${field}_缺失`])
}
function hint(row: Row, field: string): string {
  return String(row[`${field}_说明`] ?? '该字段暂无数据')
}
function lastReset(row: Row): ResetRecord | undefined {
  return row.复位历史?.[row.复位历史.length - 1]
}
function lastDeviation(row: Row): DeviationRecord | undefined {
  return row.偏差记录?.[row.偏差记录.length - 1]
}
function badgeClass(state: string): string {
  if (state === '中断') return 'offline'
  if (state === '陈旧') return 'stale'
  return 'ok'
}
function availableActions(row: Row): string[] {
  if (row.status === '已停用') return []
  if (row.status === '正常') return ['登记偏差', '办理停用']
  return ['校核复位', '办理停用']
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  loading.value = true
  loadError.value = ''
  const params = new URLSearchParams()
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      throw new Error(`接口返回 ${response.status}`)
    }
    const payload = (await response.json()) as { items?: Row[]; total?: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    // 查询失败不清空已有行：旧数据保留但顶部明确告警，与正常空态区分
    loadError.value = error instanceof Error ? error.message : '计轴设备列表读取失败'
  } finally {
    loading.value = false
  }
}

async function postAction(path: string, body: Record<string, unknown>): Promise<{ ok: boolean; message: string; entry?: Row }> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = (await response.json().catch(() => null)) as { ok: boolean; message: string; entry?: Row } | null
  if (!response.ok || !payload) {
    throw new Error(`接口返回 ${response.status}`)
  }
  return payload
}

function patchRow(entry: Row) {
  const idx = rows.value.findIndex((r) => r.id === entry.id)
  if (idx >= 0) rows.value[idx] = entry
  if (detail.value?.id === entry.id) detail.value = entry
}

async function runAction(action: string, row: Row) {
  busyId.value = row.id
  actionMessage.value = ''
  try {
    const result = await postAction(`${ENDPOINT}/${row.id}/actions`, { action })
    if (!result.ok) {
      // 复位被拒等业务失败：后端已回滚，用返回的最新状态刷新本地行
      actionOk.value = false
      actionMessage.value = result.message
      if (result.entry) patchRow(result.entry)
      return
    }
    actionOk.value = true
    actionMessage.value = result.message
    if (result.entry) patchRow(result.entry)
  } catch (error) {
    actionOk.value = false
    actionMessage.value = `${error instanceof Error ? error.message : '网络异常'}，本次操作状态未知，请刷新列表后重试`
  } finally {
    busyId.value = null
  }
}

/** 复位重试：失败后不改变任何入口状态，直接再发起一次校核复位。 */
async function retryReset(row: Row) {
  await runAction('校核复位', row)
}

async function simulate(scenario: string, row: Row) {
  busyId.value = row.id
  actionMessage.value = ''
  try {
    const result = await postAction(`${ENDPOINT}/${row.id}/simulate`, { scenario })
    actionOk.value = result.ok
    actionMessage.value = result.message
    if (result.entry) patchRow(result.entry)
  } catch (error) {
    actionOk.value = false
    actionMessage.value = error instanceof Error ? error.message : '场景模拟失败'
  } finally {
    busyId.value = null
  }
}

async function openDetail(row: Row) {
  detail.value = row
  detailError.value = ''
  await loadDetail(row.id)
}

async function loadDetail(id: number) {
  detailLoading.value = true
  detailError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${id}`)
    if (!response.ok) throw new Error(`接口返回 ${response.status}`)
    detail.value = (await response.json()) as Row
    patchRow(detail.value)
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '详情读取失败'
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detail.value = null
  detailError.value = ''
}

onMounted(reload)
</script>

<style scoped>
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px;
  color: var(--muted);
  font-size: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--border);
  border-top-color: var(--brand);
  border-radius: 50%;
  animation: axle-spin 0.8s linear infinite;
}
@keyframes axle-spin {
  to { transform: rotate(360deg); }
}
.notice {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  margin-bottom: 12px;
  border-radius: 8px;
  font-size: 13px;
}
.notice p { margin: 4px 0 0; }
.notice.error { background: #fef3f2; border: 1px solid #f04438; color: #7a271a; }
.ok-text { color: #067647; }
.cell-missing { color: var(--muted); font-style: italic; }
.cell-ok { color: #067647; }
.cell-sub { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
.deviation-text { color: #b54708; }
.badge {
  display: inline-block;
  padding: 0 6px;
  margin-left: 6px;
  border-radius: 10px;
  font-size: 11px;
  line-height: 18px;
}
.badge.offline, .badge.fail { background: #fee4e2; color: #b42318; }
.badge.stale { background: #fef0c7; color: #b54708; }
.badge.ok { background: #d1fadf; color: #067647; }
.row-abnormal { background: #fffaeb; }
.row-offline { background: #fef3f2; }
.row-actions { white-space: nowrap; }
.row-actions .link { margin-right: 8px; }
.link.retry { color: #b54708; font-weight: 600; }
.link:disabled { color: #9aa6b2; cursor: not-allowed; }
.scene { display: inline-flex; gap: 4px; margin-left: 4px; padding-left: 8px; border-left: 1px dashed var(--border); }
.conclusion-cell { max-width: 240px; color: #344054; }
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  justify-content: flex-end;
  z-index: 100;
}
.drawer {
  width: 560px;
  max-width: 92vw;
  height: 100%;
  background: #fff;
  padding: 18px 20px;
  overflow-y: auto;
  box-shadow: -8px 0 24px rgba(16, 24, 40, 0.18);
}
.drawer-head { display: flex; justify-content: space-between; align-items: flex-start; }
.drawer-head h3 { margin: 0 0 4px; }
.drawer-head p { margin: 0; font-size: 13px; }
.detail-table { width: 100%; margin-top: 12px; }
.detail-table th {
  width: 110px;
  color: var(--muted);
  font-weight: normal;
  text-align: left;
  vertical-align: top;
}
.detail-block { margin-top: 18px; }
.detail-block h4 { margin: 0 0 8px; font-size: 14px; }
.record-list { list-style: none; margin: 0; padding: 0; }
.record-list li {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-bottom: 6px;
  font-size: 13px;
}
.record-time { color: var(--muted); font-size: 12px; min-width: 132px; }
.drawer-actions { display: flex; gap: 10px; margin-top: 20px; }
.filter-item select { padding: 5px 8px; }
</style>
