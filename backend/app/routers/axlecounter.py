"""计轴设备接口：维护计轴器，覆盖登记偏差、校核复位、办理停用等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.axlecounter import AxlecounterService

router = APIRouter(prefix="/api/axlecounter", tags=["计轴设备"])

service = AxlecounterService()

LIST_FIELDS = ["计轴器编号", "所属区间", "检测磁头", "轮轴脉冲", "磁头读数", "计数偏差", "复位状态", "校核记录", "计轴状态"]
STATUSES = ["正常", "计数偏差", "磁头故障", "数据中断", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按计轴器编号检索"),
    status: str | None = Query(default=None, description="正常、计数偏差、磁头故障、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按计轴器编号与状态过滤计轴设备列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条计轴器明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"计轴器 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条计轴器，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="计轴器已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条计轴器执行登记偏差、校核复位、办理停用。

    动作被规则拦下（如数据中断时复位）时 ok=False：计轴状态回到/保持在原状态，
    偏差记录不丢，复位历史只追加一条失败审计，前端可直接重试。
    """
    action = str(payload.values.get("action") or "").strip()
    entry, message, changed = service.run_action(entry_id, action, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=changed, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出计轴设备清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "axlecounter", "total": total, "items": items}
