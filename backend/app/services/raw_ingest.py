"""原始入库 时间线服务：支持入库提交后触发字段映射规则提取，并关联创建ODS表结构。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "raw_ingest"
REQUIRED_FIELDS = ["批次编号", "来源系统", "文件格式"]
STATUS_ORDER = ["草稿", "校验中", "已入库", "已归档"]
ACTION_RULES = {"发起校验": "校验中", "确认入库": "已入库", "归档批次": "已归档"}
NEGATIVE_ACTIONS = []


class RawIngestService:
    def get_timeline(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        today = date.today()
        items: list[dict] = []
        for i, row in enumerate(rows):
            start = (today - timedelta(days=7 - i * 2)).isoformat()
            end = (today + timedelta(days=3 - i)).isoformat()
            items.append({
                "id": int(row.get("id", i + 1)),
                "name": str(row.get("批次编号", "")),
                "start": start,
                "end": end,
                "status": str(row.get("status", STATUS_ORDER[i % len(STATUS_ORDER)])),
                "logs": f"[{start}] 作业启动\n[{start}] 正在处理...\n[{end}] 任务{'完成' if row.get('status') == STATUS_ORDER[-1] else '进行中'}",
            })
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_by_batch_no(self, batch_no: str) -> dict[str, Any] | None:
        return store.find_by_field(MODULE, "批次编号", batch_no)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update({f: values.get(f) for f in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 补全其他字段
        for k in ["原始行数", "入库时间", "字符编码", "校验摘要", "落地路径"]:
            if k not in entry or not entry.get(k):
                entry[k] = values.get(k, "")
        if not entry.get("入库时间"):
            entry["入库时间"] = date.today().isoformat()
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"入库批次 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于原始入库可执行范围"
        target = ACTION_RULES[action]
        # 状态机校验：只能按顺序推进
        current_idx = STATUS_ORDER.index(entry["status"])
        target_idx = STATUS_ORDER.index(target)
        if target_idx < current_idx:
            return None, f"状态不能回退：当前{entry['status']} → 目标{target}"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 保存版本
        store.save_version(MODULE, entry_id, entry)
        # 确认入库时，自动触发字段映射提取和ODS表创建
        if action == "确认入库":
            self._on_ingest_confirmed(entry)
        return entry, f"入库批次已{action}"

    def _on_ingest_confirmed(self, entry: dict[str, Any]) -> None:
        batch_no = entry.get("批次编号", "")
        source = entry.get("来源系统", "")
        fmt = entry.get("文件格式", "")
        # 自动提取字段映射规则（模拟从文件头解析）
        from app.services.column_mapping import ColumnMappingService
        cms = ColumnMappingService()
        # 模拟解析源字段（按来源系统决定）
        if "订单" in source or "ord" in source.lower():
            src_fields = "order_id,amount,status"
            tgt_fields = "order_id,order_amount,order_status"
            dtypes = "STRING,DECIMAL,STRING"
            tbl_name = "ods_orders"
        elif "支付" in source or "pay" in source.lower():
            src_fields = "pay_id,amount,pay_time"
            tgt_fields = "pay_id,pay_amount,pay_time"
            dtypes = "STRING,DECIMAL,TIMESTAMP"
            tbl_name = "ods_payments"
        elif "用户" in source or "user" in source.lower():
            src_fields = "user_id,register_time,level"
            tgt_fields = "user_id,register_time,user_level"
            dtypes = "STRING,TIMESTAMP,STRING"
            tbl_name = "ods_users"
        else:
            src_fields = "col1,col2,col3"
            tgt_fields = "field1,field2,field3"
            dtypes = "STRING,STRING,STRING"
            tbl_name = f"ods_{source}"
        rule_no = f"MAP-{batch_no.replace('RAW-', '')}"
        cms.create_entry({
            "规则编号": rule_no,
            "源字段名": src_fields,
            "目标字段名": tgt_fields,
            "数据类型": dtypes,
            "默认值": ",,",
            "转换函数": "trim,trim,trim",
            "规则版本": "v1",
            "生效日期": date.today().isoformat(),
            "来源批次": batch_no,
        })
        # 自动创建/更新ODS表结构
        from app.services.ods_layer import OdsLayerService
        ods = OdsLayerService()
        existing = store.find_by_field("ods_layer", "表名称", tbl_name)
        if existing is None:
            existing, _ = ods.create_entry({
                "表编号": f"ODS_{batch_no.replace('RAW-', '').replace('-', '_')}",
                "来源系统": source,
                "表名称": tbl_name,
                "分区字段": "dt",
                "存储格式": fmt if fmt else "PARQUET",
                "保留天数": "90",
                "压缩算法": "SNAPPY",
                "创建批次": batch_no,
                "字段列表": tgt_fields,
            })
        else:
            # 更新版本
            existing["创建批次"] = batch_no
            existing["字段列表"] = tgt_fields
            store.save_version("ods_layer", existing["id"], existing)
        # 按来源归属裁剪入库批次字段结构，生成ODS可见字段结构（只暴露归属字段）
        from app.services.ods_visible_schema import OdsVisibleSchemaService
        from app.services.source_attribution import split_fields
        ovs = OdsVisibleSchemaService()
        tgt_list = split_fields(tgt_fields)
        structure = None
        if existing is not None:
            structure, _ = ovs.generate_for_table(existing, system=True, batch_fields=tgt_list)
        visible_set = set(split_fields(structure.get("可见字段清单"))) if structure else set(tgt_list)
        dtype_list = split_fields(dtypes)
        kept = [(f, dtype_list[i] if i < len(dtype_list) else "STRING")
                for i, f in enumerate(tgt_list) if f in visible_set]
        visible_text = ",".join(f for f, _ in kept)
        visible_dtypes = ",".join(d for _, d in kept)
        # 自动注册schema：只注册裁剪后的可见字段，越权字段不写入结构
        from app.services.schema_registry import SchemaRegistryService
        sch = SchemaRegistryService()
        next_version = f"v{len(sch.get_schema_for_table(tbl_name)) + 1}"
        sch.create_entry({
            "定义编号": f"SCH-{batch_no.replace('RAW-', '')}",
            "表名称": tbl_name,
            "字段名称": visible_text,
            "字段类型": visible_dtypes,
            "可空标识": ",".join("N" for _ in kept),
            "默认值": ",".join("" for _ in kept),
            "定义版本": next_version,
            "生效批次": batch_no,
        })
        # 写入模拟表数据（按可见字段落地，下游查询读不到越权字段）
        row_count = int(entry.get("原始行数", "0") or "0")
        if row_count > 0:
            visible_fields = [f for f, _ in kept]
            sample_rows = []
            for i in range(min(row_count, 10)):  # 最多模拟10条
                sample_rows.append({field: f"{field}_{i + 1}" for field in visible_fields})
            store.set_table_rows(tbl_name, sample_rows)
