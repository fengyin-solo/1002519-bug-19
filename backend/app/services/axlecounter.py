"""计轴设备业务规则：状态流转、字段校验、采集数据兜底与复位回滚都收在这里。

边界口径：
- 轮轴脉冲、校核记录等采集项允许为空，序列化时逐字段标注“暂无”及缺失原因，
  不再让前端拿到空白单元格无法判断是没采集还是漏录。
- 校核复位遇到磁头读数中断/陈旧（拿不到本次校核基准）时判定失败：
  状态整体回滚到复位前，偏差记录保留，复位历史只追加一条失败记录，不覆盖旧历史。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "axlecounter"
REQUIRED_FIELDS = ["计轴器编号", "所属区间", "检测磁头"]
STATUS_ORDER = ["正常", "计数偏差", "磁头故障", "已停用"]
ACTION_RULES = {"登记偏差": "计数偏差", "校核复位": "正常", "办理停用": "已停用"}

# 采集/校核类字段：为空时需要逐项说明缺的是什么
PULSE_FIELD = "轮轴脉冲"
DEVIATION_FIELD = "计数偏差"
RESET_FIELD = "复位状态"
CHECK_FIELD = "校核记录"
READING_FIELD = "磁头读数"
STATE_FIELD = "计轴状态"

MISSING_HINTS = {
    PULSE_FIELD: "未采集轮轴脉冲",
    DEVIATION_FIELD: "无在案计数偏差",
    RESET_FIELD: "未执行过复位",
    CHECK_FIELD: "暂无校核记录",
    READING_FIELD: "磁头读数缺失",
}

READING_NORMAL = "正常"
READING_STALE = "陈旧"
READING_INTERRUPTED = "中断"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_blank(value: Any) -> bool:
    return value is None or not str(value).strip()


class AxlecounterService:
    # ------------------------------------------------------------------ 查询
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("计轴器编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self.serialize(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self.serialize(row) if row is not None else None

    # ------------------------------------------------------------ 序列化/兜底
    def serialize(self, entry: dict[str, Any]) -> dict[str, Any]:
        """统一出口：列表与详情都走这里，保证刷新后两边结论一致。"""
        result = dict(entry)

        missing_fields: list[str] = []
        for field, hint in MISSING_HINTS.items():
            value = entry.get(field)
            if _is_blank(value):
                result[field] = None
                result[f"{field}_缺失"] = True
                result[f"{field}_说明"] = hint
                missing_fields.append(field)
            else:
                result[f"{field}_缺失"] = False
                result[f"{field}_说明"] = ""

        reading_state = entry.get("磁头读数状态") or READING_NORMAL
        result["磁头读数状态"] = reading_state
        if reading_state == READING_INTERRUPTED:
            result["磁头读数_说明"] = "数据采集中断，读数不可用（当前显示的是中断前缓存值，请勿作为依据）"
        elif reading_state == READING_STALE:
            result["磁头读数_说明"] = "读数超过校核时限，已标记为陈旧值"

        result["缺失字段"] = missing_fields
        result["结论"] = self._conclusion(entry, reading_state)
        result["可复位"] = self._reset_blocker(entry, reading_state) is None
        result["复位历史"] = list(entry.get("复位历史") or [])
        result["偏差记录"] = list(entry.get("偏差记录") or [])
        result["偏差记录_已闭环"] = list(entry.get("偏差记录_已闭环") or [])
        return result

    @staticmethod
    def _conclusion(entry: dict[str, Any], reading_state: str) -> str:
        status = entry.get("status")
        # 采集链路异常优先于业务状态：没有可信读数时，“正常”结论不成立
        if reading_state == READING_INTERRUPTED:
            if status == "磁头故障":
                return "检测磁头故障导致采集中断，计轴判定不可信，需修复磁头"
            return "采集中断，暂无法给出计轴结论（读数停留在中断前，请勿作为依据）"
        if status == "正常":
            return "计轴正常，进出轴计数平衡"
        if status == "计数偏差":
            latest = (entry.get("偏差记录") or [{}])[-1]
            detail = f"：{latest.get('说明', '')}" if latest.get("说明") else ""
            return f"存在计数偏差，需校核复位{detail}"
        if status == "磁头故障":
            return "检测磁头故障，计轴判定不可信，需修复磁头"
        if status == "已停用":
            return "计轴器已停用，不参与区间占用判定"
        if reading_state == READING_STALE:
            return "磁头读数已陈旧，暂无法给出可信计轴结论"
        return "计轴状态未知，请核对采集链路"

    @staticmethod
    def _reset_blocker(entry: dict[str, Any], reading_state: str) -> str | None:
        """复位前置条件不满足时返回人能读懂的原因。"""
        if entry.get("status") == "已停用":
            return "计轴器已停用，停用状态下不允许复位，请先办理恢复启用"
        if reading_state == READING_INTERRUPTED:
            return "与检测磁头通信中断，拿不到本次校核基准，复位未执行"
        if reading_state == READING_STALE:
            return "磁头读数已陈旧，无法确认当前轴数，复位未执行"
        return None

    # ------------------------------------------------------------------ 登记
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 新登记设备尚无任何采集：各采集项显式置空并说明，而不是写占位文本
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry[PULSE_FIELD] = None
        entry["脉冲时间"] = None
        entry[DEVIATION_FIELD] = None
        entry[RESET_FIELD] = "未复位"
        entry[CHECK_FIELD] = None
        entry["校核时间"] = None
        entry[READING_FIELD] = None
        entry["磁头读数状态"] = READING_INTERRUPTED
        entry["读数时间"] = None
        entry["偏差轴数"] = 0
        entry["偏差记录"] = []
        entry["复位历史"] = []
        entry[STATE_FIELD] = "采集链路未接通"
        rows.append(entry)
        return self.serialize(entry), []

    # ------------------------------------------------------------------ 动作
    def run_action(
        self, entry_id: int, action: str
    ) -> tuple[dict[str, Any] | None, str, bool]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"计轴器 {entry_id} 不存在或已归档", False
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于计轴设备可执行范围", False

        if action == "登记偏差":
            return self._register_deviation(entry)
        if action == "校核复位":
            return self._reset(entry)
        return self._deactivate(entry)

    def _register_deviation(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        if entry.get("status") == "已停用":
            return self.serialize(entry), "计轴器已停用，不能再登记偏差", False
        amount = 2  # 演示口径：每次登记偏差按 +2 轴记账
        previous = int(entry.get("偏差轴数") or 0)
        record = {
            "时间": _now(),
            "偏差轴数": f"+{amount}",
            "说明": f"进出轴计数相差 {amount} 轴（入轴多于出轴），待人工校核",
        }
        entry.setdefault("偏差记录", []).append(record)
        entry["偏差轴数"] = previous + amount
        entry[DEVIATION_FIELD] = f"+{entry['偏差轴数']} 轴"
        entry["偏差时间"] = record["时间"]
        entry["status"] = "计数偏差"
        entry["pending"] = True
        entry["abnormal"] = True
        entry[STATE_FIELD] = "计数不平衡，区间占用待确认"
        return self.serialize(entry), f"已登记计数偏差 +{amount} 轴，请现场核查后办理校核复位", True

    def _reset(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        reading_state = entry.get("磁头读数状态") or READING_NORMAL
        blocker = self._reset_blocker(entry, reading_state)

        # 失败路径：先把失败写进历史（只追加），再整体回滚业务字段
        history = entry.setdefault("复位历史", [])
        if blocker is not None:
            history.append({
                "时间": _now(),
                "结果": "失败",
                "复位前状态": entry.get("status"),
                "说明": blocker,
            })
            # 回滚即“什么都不改”：显式确认复位状态、偏差记录、磁头读数维持原值
            entry[RESET_FIELD] = entry.get(RESET_FIELD) or "复位失败（状态未变）"
            return self.serialize(entry), f"校核复位失败：{blocker}，计轴状态维持「{entry.get('status')}」，可重试", False

        # 成功路径：先留快照，再改状态，历史追加而不是覆盖
        snapshot_status = entry.get("status")
        checked_at = _now()
        check_record = f"现场人工校核，进出轴计数平衡（{checked_at}）"
        history.append({
            "时间": checked_at,
            "结果": "成功",
            "复位前状态": snapshot_status,
            "说明": check_record,
        })
        entry["status"] = "正常"
        entry["pending"] = True
        entry["abnormal"] = False
        entry[RESET_FIELD] = f"已复位（{checked_at}）"
        entry[CHECK_FIELD] = check_record
        entry["校核时间"] = checked_at
        # 偏差已闭环：清掉在案偏差显示，但每条偏差都归档保留，且不覆盖更早的归档
        if entry.get("偏差记录"):
            entry["偏差记录_已闭环"] = list(entry.get("偏差记录_已闭环") or []) + list(entry["偏差记录"])
        entry["偏差记录"] = []
        entry[DEVIATION_FIELD] = None
        entry["偏差轴数"] = 0
        entry["偏差时间"] = None
        if reading_state != READING_NORMAL:
            entry["磁头读数状态"] = READING_NORMAL
        entry[READING_FIELD] = f"0 / 0 轴（复位基准）"
        entry["读数时间"] = checked_at
        entry[STATE_FIELD] = "计数平衡，区间空闲"
        return self.serialize(entry), "校核复位成功，计数已清零对齐，偏差记录归档保留", True

    def _deactivate(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        entry["status"] = "已停用"
        entry["pending"] = False
        entry["abnormal"] = False
        entry[STATE_FIELD] = "设备已停用"
        return self.serialize(entry), "计轴器已办理停用", True

    # ------------------------------------------------------------ 场景模拟
    def simulate(self, entry_id: int, scenario: str) -> tuple[dict[str, Any] | None, str, bool]:
        """演示采集中断/读数陈旧/链路恢复，便于验证复位回滚与重试。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"计轴器 {entry_id} 不存在或已归档", False
        at = _now()
        if scenario == "interrupt":
            entry["磁头读数状态"] = READING_INTERRUPTED
            # 保留最后一次读数作为缓存值，但打上时间与状态标签，前端不得当成新值
            entry["读数时间"] = f"{at}（中断，读数停留在中断前）"
            return self.serialize(entry), "已模拟采集中断：磁头读数停留在中断前缓存值，复位将被拒绝", True
        if scenario == "stale":
            entry["磁头读数状态"] = READING_STALE
            entry["读数时间"] = f"{at}（超出校核时限）"
            return self.serialize(entry), "已模拟读数陈旧：复位将被拒绝", True
        if scenario == "recover":
            entry["磁头读数状态"] = READING_NORMAL
            entry[READING_FIELD] = f"{entry.get('偏差轴数', 0)} / {entry.get('偏差轴数', 0)} 轴（链路恢复）"
            entry["读数时间"] = at
            return self.serialize(entry), "采集链路已恢复，可以重新办理校核复位", True
        return self.serialize(entry), f"未知场景「{scenario}」，支持：interrupt、stale、recover", False
