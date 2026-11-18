"""贴源层管理接口：维护贴源表，覆盖提交校验、确认上线、标记下线等动作。

ODS可见字段结构子接口：按来源归属裁剪入库批次字段结构，支持生成/重裁剪、
切换来源归属、状态单向推进，以及详情页所需的比对与冲突信息。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionPayload,
    ActionResult,
    EntryPayload,
    PageResult,
    VisibleGeneratePayload,
    VisibleSwitchPayload,
)
from app.services.ods_layer import OdsLayerService
from app.services.ods_visible_schema import OdsVisibleSchemaService
from app.services.source_attribution import SourceAttributionService

router = APIRouter(prefix="/api/ods_layer", tags=["贴源层管理"])

service = OdsLayerService()
visible_service = OdsVisibleSchemaService()
attribution_service = SourceAttributionService()

LIST_FIELDS = ["表编号", "来源系统", "表名称", "分区字段", "存储格式", "保留天数", "压缩算法", "创建批次"]
STATUSES = ["草稿", "待校验", "已上线", "已下线"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按表编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已上线、已下线"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、表编号关键字与状态过滤贴源层管理列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ods_layer.csv"'})


@router.get("/attributions")
def list_attributions() -> dict[str, object]:
    """来源归属清单：每个来源系统的归属字段与权限级别，供切换归属时选择。

    必须声明在 /{entry_id} 之前，否则会被详情路由截获。
    """
    return {"items": attribution_service.list_attributions()}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条贴源表明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"贴源表 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条贴源表，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="贴源表已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条贴源表执行提交校验、确认上线、标记下线；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/batches")
def get_related_batches(entry_id: int) -> dict[str, Any]:
    """返回与该ODS表关联的入库批次。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"贴源表 {entry_id} 不存在")
    batches = service.get_batches_for_table(entry.get("表名称", ""))
    return {"items": batches}


@router.get("/{entry_id}/data")
def get_table_data(entry_id: int) -> dict[str, Any]:
    """返回该ODS表的下游查询数据：按可见字段结构投影，越权字段不出现。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"贴源表 {entry_id} 不存在")
    data = service.get_visible_table_data(entry.get("表名称", ""))
    return {"items": data}


@router.get("/{entry_id}/visible")
def get_visible_schema(entry_id: int) -> dict[str, Any]:
    """详情页比对视图：贴源表、来源归属、ODS可见字段结构、逐字段比对与冲突信息。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"贴源表 {entry_id} 不存在")
    table_name = entry.get("表名称", "")
    structure = visible_service.get_by_table(table_name)
    attribution = attribution_service.get_by_source(entry.get("来源系统", ""))
    batches = service.get_batches_for_table(table_name)
    return {
        "entry": entry,
        "structure": structure,
        "attribution": attribution,
        "batch": batches[0] if batches else None,
        "compare": visible_service.compare_for_table(entry),
        "conflict": visible_service.conflict_report(entry),
    }


@router.post("/{entry_id}/visible/generate", response_model=ActionResult)
def generate_visible_schema(entry_id: int, payload: VisibleGeneratePayload) -> ActionResult:
    """生成/重裁剪ODS可见字段结构；只读、受控、共享的归属内容不能改动。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"贴源表 {entry_id} 不存在")
    structure, message = visible_service.generate_for_table(entry, boundary=payload.boundary)
    if structure is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=structure)


@router.post("/{entry_id}/visible/switch", response_model=ActionResult)
def switch_attribution(entry_id: int, payload: VisibleSwitchPayload) -> ActionResult:
    """切换来源归属并按新归属重新裁剪；未登记的目标归属会被拒绝。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"贴源表 {entry_id} 不存在")
    structure, message = visible_service.switch_attribution(
        entry, payload.source, boundary=payload.boundary)
    if structure is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=structure)


@router.post("/{entry_id}/visible/actions", response_model=ActionResult)
def run_visible_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """推进ODS可见字段结构状态：单向逐级，权限与归属校验拒绝跳级。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"贴源表 {entry_id} 不存在")
    structure, message = visible_service.run_action(entry, payload.action.strip())
    if structure is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=structure)
