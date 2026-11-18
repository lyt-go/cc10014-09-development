"""贴源层管理业务规则：支持从入库批次创建表结构，存储模拟表数据，并可被下游DWD消费。"""
from __future__ import annotations

from typing import Any

from app.services.ods_visible_schema import OdsVisibleSchemaService
from app.services.source_attribution import split_fields
from app.store import store

MODULE = "ods_layer"
LIST_FIELDS = ["表编号", "来源系统", "表名称", "分区字段", "存储格式", "保留天数", "压缩算法", "创建批次"]
REQUIRED_FIELDS = ["表编号", "来源系统", "表名称"]
STATUS_ORDER = ["草稿", "待校验", "已上线", "已下线"]
ACTION_RULES = {"提交校验": "待校验", "确认上线": "已上线", "标记下线": "已下线"}
NEGATIVE_ACTIONS = []

visible_service = OdsVisibleSchemaService()


class OdsLayerService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("表编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return [self._with_visible_info(row) for row in rows[start:start + size]], total

    def _with_visible_info(self, row: dict[str, Any]) -> dict[str, Any]:
        """列表行附带ODS可见字段结构状态与越权字段数，不改动仓库里的原始行。"""
        structure = visible_service.get_by_table(str(row.get("表名称", "")))
        enriched = dict(row)
        if structure is None:
            enriched["可见结构状态"] = "未生成"
            enriched["越权字段数"] = 0
        else:
            enriched["可见结构状态"] = str(structure.get("status", ""))
            enriched["越权字段数"] = len(split_fields(structure.get("越权字段清单")))
        return enriched

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_by_table_name(self, table_name: str) -> dict[str, Any] | None:
        return store.find_by_field(MODULE, "表名称", table_name)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        if values.get("字段列表"):
            entry["字段列表"] = values["字段列表"]
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"贴源表 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于贴源层管理可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "确认上线":
            # 来源归属和贴源表上线必须一起校验：越权字段未裁剪不能上线
            ok, message = visible_service.validate_online(entry)
            if not ok:
                return None, message
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.save_version(MODULE, entry_id, entry)
        return entry, f"贴源表已{action}"

    def get_table_data(self, table_name: str) -> list[dict[str, Any]]:
        return store.table_rows(table_name)

    def get_visible_table_data(self, table_name: str) -> list[dict[str, Any]]:
        """下游查询视角的表数据：按ODS可见字段结构投影，越权字段不出现。"""
        return visible_service.project_rows(table_name, store.table_rows(table_name))

    def get_batches_for_table(self, table_name: str) -> list[dict[str, Any]]:
        ods = self.get_by_table_name(table_name)
        if not ods:
            return []
        batch_no = ods.get("创建批次")
        if not batch_no:
            return []
        from app.services.raw_ingest import RawIngestService
        ri = RawIngestService()
        batch = ri.get_by_batch_no(batch_no)
        return [batch] if batch else []
