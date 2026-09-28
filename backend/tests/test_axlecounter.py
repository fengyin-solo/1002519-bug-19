"""计轴设备边界场景测试。

覆盖：缺失字段判定（未采集/漏录）、查询异常区分、复位失败回滚、
偏差与复位历史只追加不覆盖、列表与详情结论一致、磁头旧值标记。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.axlecounter import MODULE, AxlecounterService
from app.store import Store
import app.services.axlecounter as ax_module


@pytest.fixture
def client(monkeypatch) -> TestClient:
    # 每个用例独立的内存仓库，避免相互污染
    fresh = Store()
    monkeypatch.setattr(ax_module, "store", fresh)
    return TestClient(app)


def find(client: TestClient, entry_id: int) -> dict:
    resp = client.get(f"/api/axlecounter/{entry_id}")
    assert resp.status_code == 200
    return resp.json()


def action(client: TestClient, entry_id: int, action: str, **values):
    body = {"values": {"action": action, **values}}
    resp = client.post(f"/api/axlecounter/{entry_id}/actions", json=body)
    assert resp.status_code == 200
    return resp.json()


# ---- 缺失数据：暂无 + 缺的是哪一项、是未采集还是漏录 ----------------------

def test_missing_pulse_when_link_down_is_uncollected(client: TestClient) -> None:
    detail = find(client, 4)
    missing = detail["缺失字段"]
    assert "轮轴脉冲" in missing
    assert missing["轮轴脉冲"]["类型"] == "未采集"
    assert "中断" in missing["轮轴脉冲"]["说明"]
    # 人工校核记录在中断场景下仍属于漏录，不能混成“未采集”
    assert missing["校核记录"]["类型"] == "漏录"


def test_missing_pulse_when_online_is_omitted(client: TestClient) -> None:
    # 设备正常但脉冲为空：说明是记录环节漏录，不是采集不到
    detail = find(client, 5)
    missing = detail["缺失字段"]
    assert missing["轮轴脉冲"]["类型"] == "漏录"
    assert missing["校核记录"]["类型"] == "漏录"
    assert "磁头读数" not in missing


def test_list_and_detail_share_same_conclusion(client: TestClient) -> None:
    listed = {row["id"]: row for row in client.get("/api/axlecounter").json()["items"]}
    for entry_id, row in listed.items():
        detail = find(client, entry_id)
        assert row["结论"] == detail["结论"] == row["status"]
        assert row["结论说明"] == detail["结论说明"]
        assert row["缺失字段"] == detail["缺失字段"]


# ---- 查询异常与正常数据区分 ---------------------------------------------

def test_list_with_data_is_not_empty_state(client: TestClient) -> None:
    payload = client.get("/api/axlecounter").json()
    assert payload["total"] == 6
    assert len(payload["items"]) == 6


def test_filter_no_match_returns_empty_page_not_error(client: TestClient) -> None:
    resp = client.get("/api/axlecounter", params={"keyword": "NOT-EXIST"})
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_oversized_page_is_explicit_400(client: TestClient) -> None:
    resp = client.get("/api/axlecounter", params={"size": 500})
    assert resp.status_code == 400
    assert "最多" in resp.json()["detail"]


def test_missing_entry_returns_readable_404(client: TestClient) -> None:
    resp = client.get("/api/axlecounter/999")
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


# ---- 复位失败：回滚原状态、偏差保留、历史不覆盖 --------------------------

def test_reset_during_interrupt_fails_and_rolls_back(client: TestClient) -> None:
    before = find(client, 4)
    deviation_before = before["偏差记录"]
    history_len = len(before["复位历史"])

    result = action(client, 4, "校核复位")
    assert result["ok"] is False
    assert "数据中断" in result["message"]

    after = find(client, 4)
    # 状态回到/保持在原计轴状态
    assert after["status"] == "数据中断"
    assert after["结论"] == "数据中断"
    # 偏差记录一条不丢
    assert after["偏差记录"] == deviation_before
    # 复位历史追加失败审计，不覆盖原记录
    assert len(after["复位历史"]) == history_len + 1
    assert after["复位历史"][-1]["结果"] == "失败"
    assert all(item["时间"] for item in after["复位历史"])


def test_failed_reset_can_be_retried_after_condition_cleared(client: TestClient) -> None:
    # id=6 首次复位因授权超时失败
    first = action(client, 6, "校核复位")
    assert first["ok"] is False
    rolled_back = find(client, 6)
    assert rolled_back["status"] == "计数偏差"
    assert len(rolled_back["偏差记录"]) == 1

    # 重试：条件已清除，应成功，且失败记录仍留在历史里
    second = action(client, 6, "校核复位")
    assert second["ok"] is True
    after = find(client, 6)
    assert after["status"] == "正常"
    results = [item["结果"] for item in after["复位历史"]]
    assert results == ["失败", "成功"]
    # 复位成功也不能清掉偏差记录
    assert len(after["偏差记录"]) == 1


def test_register_deviation_appends_and_survives_reset(client: TestClient) -> None:
    action(client, 1, "登记偏差", 偏差值="2 轴", 说明="测试补登")
    mid = find(client, 1)
    assert mid["status"] == "计数偏差"
    assert len(mid["偏差记录"]) == 1

    result = action(client, 1, "校核复位")
    assert result["ok"] is True
    after = find(client, 1)
    assert after["status"] == "正常"
    assert len(after["偏差记录"]) == 1, "成功复位后偏差记录必须留存"
    assert after["偏差记录"][0]["偏差值"] == "2 轴"


def test_deviation_blocked_on_faulty_head_keeps_state(client: TestClient) -> None:
    result = action(client, 3, "登记偏差")
    assert result["ok"] is False
    assert find(client, 3)["status"] == "磁头故障"


def test_reset_on_normal_device_fails_without_changing_history(client: TestClient) -> None:
    before = find(client, 1)
    history_len = len(before["复位历史"])
    result = action(client, 1, "校核复位")
    assert result["ok"] is False
    after = find(client, 1)
    assert after["status"] == "正常"
    assert len(after["复位历史"]) == history_len + 1
    assert after["复位历史"][-1]["结果"] == "失败"


# ---- 磁头读数：中断期间旧值必须显式标记 ----------------------------------

def test_stale_reading_marked_during_interrupt(client: TestClient) -> None:
    detail = find(client, 4)
    assert detail["磁头读数"] == "A:177 / B:177"
    assert detail["读数时间"] == "2026-09-27 23:42"
    assert detail["磁头读数陈旧"] is True
    assert "旧值" in detail["结论说明"]


def test_fresh_reading_not_marked_stale(client: TestClient) -> None:
    detail = find(client, 1)
    assert detail["磁头读数陈旧"] is False
    assert detail["磁头读数"] == "A:312 / B:312"


def test_faulty_head_without_reading_reports_missing(client: TestClient) -> None:
    detail = find(client, 3)
    assert detail["磁头读数"] is None
    # 磁头故障下没有读数属于漏录（设备未上报），与数据中断的“未采集”区分
    assert detail["缺失字段"]["磁头读数"]["类型"] == "漏录"


# ---- 状态过滤包含新增的“数据中断” ---------------------------------------

def test_filter_by_interrupt_status(client: TestClient) -> None:
    resp = client.get("/api/axlecounter", params={"status": "数据中断"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["计轴器编号"] for item in items] == ["AXLE-0004"]


# ---- service 层直接验证创建与未知动作 ------------------------------------

def test_create_entry_initializes_history_lists() -> None:
    service = AxlecounterService()
    entry, missing = service.create_entry({"计轴器编号": "AXLE-NEW", "所属区间": "区间", "检测磁头": "磁头"})
    assert missing == []
    assert entry["偏差记录"] == []
    assert entry["复位历史"] == []
    assert entry["缺失字段"]["轮轴脉冲"]["类型"] == "漏录"


def test_unknown_action_rejected() -> None:
    service = AxlecounterService()
    entry, message, changed = service.run_action(1, "随意操作")
    assert entry is None
    assert changed is False
    assert "不属于" in message
