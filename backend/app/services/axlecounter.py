"""计轴设备业务规则：状态流转、字段校验、缺失判定与结论口径都收在这里。

列表页与详情页共用 present() 派生展示数据，保证刷新后两边结论一致；
偏差记录、复位历史只追加不改写，校核复位失败时回滚到原计轴状态。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "axlecounter"
REQUIRED_FIELDS = ["计轴器编号", "所属区间", "检测磁头"]
# 数据中断也是一种在线计轴状态，不能简单当成停用或故障丢掉
STATUS_ORDER = ["正常", "计数偏差", "磁头故障", "数据中断", "已停用"]

# 采集类字段：数据中断时拿不到属于“未采集”；其余情况下为空属于漏录
COLLECTED_FIELDS = ["轮轴脉冲", "磁头读数"]
# 人工记录类字段：为空一律属于“漏录”
MANUAL_FIELDS = ["校核记录"]

ACTION_RULES = {"登记偏差": "计数偏差", "校核复位": "正常", "办理停用": "已停用"}
NEGATIVE_ACTIONS = []

# 复位前置状态：只有处于计数偏差的设备才允许执行校核复位
RESETTABLE_STATUSES = {"计数偏差"}
# 可以登记偏差的状态
DEVIATION_STATUSES = {"正常", "计数偏差"}

MISSING_TEXT = {
    "轮轴脉冲": "脉冲记录出现缺口，请补传采集数据",
    "磁头读数": "没有任何磁头读数上报，请检查采集通道",
    "校核记录": "缺少人工校核记录，请安排复核并补录",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class AxlecounterService:
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
        page_rows = [self.present(row) for row in rows[start:start + size]]
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self.present(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 新设备尚无采集/复位数据，用空列表而不是缺键，后续一律按追加处理
        entry["轮轴脉冲"] = None
        entry["校核记录"] = None
        entry["reading"] = None
        entry["偏差记录"] = []
        entry["复位历史"] = []
        rows.append(entry)
        return self.present(entry), []

    # ---- 动作 -------------------------------------------------------------

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str, bool]:
        """执行状态动作。

        返回 (最新展示数据, 说明, 是否生效)。失败时不改动任何业务字段，
        只往复位历史追加一条失败审计，调用方可以原样重试。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"计轴器 {entry_id} 不存在或已归档", False
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于计轴设备可执行范围", False
        values = values or {}

        if action == "登记偏差":
            return self._register_deviation(entry, values)
        if action == "校核复位":
            return self._reset(entry)
        return self._deactivate(entry)

    def _register_deviation(self, entry: dict[str, Any], values: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        status = str(entry.get("status") or "")
        if status not in DEVIATION_STATUSES:
            hint = {
                "磁头故障": "磁头故障尚未排除，计数不可信，请先处理磁头故障",
                "数据中断": "采集数据中断，无法确认计数，请待链路恢复后再登记",
                "已停用": "计轴器已停用，停用期间不再登记偏差",
            }.get(status, f"当前计轴状态为「{status}」，不能登记偏差")
            return self.present(entry), hint, False

        record = {
            "时间": _now(),
            "偏差值": str(values.get("偏差值") or "").strip() or "未量化（待人工复核）",
            "说明": str(values.get("说明") or "").strip() or "轮轴计数与校核计数不一致，已登记计数偏差",
        }
        # 关键：偏差记录只追加，任何动作都不得覆盖或清空
        entry.setdefault("偏差记录", []).append(record)
        entry["status"] = "计数偏差"
        entry["pending"] = True
        entry["abnormal"] = True
        return self.present(entry), f"已登记计数偏差（{record['偏差值']}），请复核后执行校核复位", True

    def _reset(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        status = str(entry.get("status") or "")
        history = entry.setdefault("复位历史", [])

        def fail(reason: str) -> tuple[dict[str, Any], str, bool]:
            # 失败也要留痕，但只能追加；业务字段与状态原样保留
            history.append({"时间": _now(), "动作": "校核复位", "结果": "失败", "说明": reason})
            return self.present(entry), f"校核复位未生效：{reason}。计轴状态仍为「{status}」，可重试", False

        if status == "正常":
            return fail("计轴计数与校核计数一致，无需复位")
        if status == "磁头故障":
            return fail("检测磁头故障未排除，读数不可信")
        if status == "数据中断":
            return fail("采集数据中断，无法完成两端计数校核")
        if status == "已停用":
            return fail("计轴器已停用，停用期间不能复位")
        if entry.get("_reset_should_fail"):
            # 演示/外部条件不满足（例如联锁未授权）时的失败路径
            entry.pop("_reset_should_fail", None)
            return fail("复位条件未确认（联锁授权超时），请重新发起")
        if status not in RESETTABLE_STATUSES:
            return fail(f"当前计轴状态「{status}」不允许复位")

        # 成功路径：先记录再改状态，保证异常中断也不会留下“状态改了历史没记”
        history.append({
            "时间": _now(),
            "动作": "校核复位",
            "结果": "成功",
            "说明": f"人工复核确认，偏差记录 {len(entry.get('偏差记录', []))} 条留存备查，计数清零",
        })
        entry["status"] = "正常"
        entry["pending"] = True
        entry["abnormal"] = False
        return self.present(entry), "校核复位成功，计轴状态恢复正常；历史偏差记录已留存", True

    def _deactivate(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        status = str(entry.get("status") or "")
        if status == "已停用":
            return self.present(entry), "计轴器已是停用状态，无需重复办理", False
        entry.setdefault("复位历史", []).append({
            "时间": _now(),
            "动作": "办理停用",
            "结果": "成功",
            "说明": f"由「{status}」办理停用，停用前偏差记录留存 {len(entry.get('偏差记录', []))} 条",
        })
        entry["status"] = "已停用"
        entry["pending"] = False
        return self.present(entry), "计轴器已办理停用", True

    # ---- 展示派生（列表与详情唯一口径） -----------------------------------

    def present(self, entry: dict[str, Any]) -> dict[str, Any]:
        """把存储记录派生成接口展示结构。原始记录不被修改。"""
        data = deepcopy(entry)
        data.pop("_reset_should_fail", None)

        status = str(entry.get("status") or "")
        reading = entry.get("reading") or None
        deviation_records = list(entry.get("偏差记录") or [])
        reset_history = list(entry.get("复位历史") or [])

        latest_deviation = deviation_records[-1] if deviation_records else None
        latest_reset = reset_history[-1] if reset_history else None

        data["计轴状态"] = status
        data["计数偏差"] = latest_deviation["偏差值"] if latest_deviation else None
        data["复位状态"] = (
            f"{latest_reset['动作']}{latest_reset['结果']}（{latest_reset['时间']}）"
            if latest_reset else None
        )
        data["磁头读数"] = reading.get("value") if reading else None
        data["读数时间"] = reading.get("time") if reading else None
        data["磁头读数陈旧"] = bool(reading and reading.get("stale"))
        data["偏差记录"] = deviation_records
        data["复位历史"] = reset_history
        data["缺失字段"] = self._missing_fields(entry, status)
        data["结论"] = status
        data["结论说明"] = self._conclusion_text(status, reading, latest_deviation)
        data["可执行动作"] = self._available_actions(status)
        return data

    def _missing_fields(self, entry: dict[str, Any], status: str) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}

        def is_empty(value: Any) -> bool:
            return value is None or not str(value).strip()

        for field in COLLECTED_FIELDS:
            value = entry.get(field)
            if field == "磁头读数":
                reading = entry.get("reading") or None
                value = reading.get("value") if reading else None
            if not is_empty(value):
                continue
            if status == "数据中断":
                kind, reason = "未采集", "采集链路中断期间没有数据上报，链路恢复前无法补采"
            else:
                kind, reason = "漏录", MISSING_TEXT[field]
            result[field] = {"类型": kind, "说明": reason}

        for field in MANUAL_FIELDS:
            if is_empty(entry.get(field)):
                suffix = "，计数偏差后仍未补录" if status == "计数偏差" else ""
                result[field] = {"类型": "漏录", "说明": MISSING_TEXT[field] + suffix}

        return result

    def _conclusion_text(
        self,
        status: str,
        reading: dict[str, Any] | None,
        latest_deviation: dict[str, Any] | None,
    ) -> str:
        if status == "正常":
            return "轮轴计数与校核计数一致，计轴设备运行正常。"
        if status == "计数偏差":
            value = latest_deviation["偏差值"] if latest_deviation else "未量化"
            return f"轮轴计数与校核计数不一致（{value}），需人工复核；复核通过后可执行校核复位。"
        if status == "磁头故障":
            return "检测磁头故障，磁头读数不可信；请先排除磁头故障，再登记偏差或校核复位。"
        if status == "数据中断":
            stale_time = reading.get("time") if reading else None
            stale_part = f"最新磁头读数停留在 {stale_time}，仅为中断前旧值；" if stale_time else ""
            return f"采集数据中断，{stale_part}中断期间轮轴脉冲未采集，校核复位不会生效，链路恢复后可重试。"
        if status == "已停用":
            return "计轴器已办理停用，停用期间不参与计数，历史偏差与复位记录继续留存。"
        return f"当前计轴状态：{status}。"

    def _available_actions(self, status: str) -> list[str]:
        if status == "已停用":
            return []
        actions = ["登记偏差", "校核复位", "办理停用"]
        return actions
