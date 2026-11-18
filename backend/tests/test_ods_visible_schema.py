"""测试：按来源归属裁剪入库批次字段结构（ODS可见字段结构）。

验证：
- 按来源归属裁剪生成ODS可见字段结构，只暴露归属字段清单
- 来源归属和贴源表上线一起校验，越权字段未裁剪不能上线
- 可见字段结构状态单向逐级推进，权限与归属拒绝跳级
- 只读、受控、共享内容不能改动，越权请求被拒绝
- 结构注册跨模块校验ODS可见字段结构
- 来源系统名称变更与历史批次归属冲突：严格/放宽两种边界
- 冲突贴源表按创建批次排处理顺序（谁优先、哪个先处理）
- 切换来源归属后越权字段不出现在结构和下游查询中
"""
from __future__ import annotations

from app.services.ods_layer import OdsLayerService
from app.services.ods_visible_schema import OdsVisibleSchemaService
from app.services.raw_ingest import RawIngestService
from app.services.schema_registry import SchemaRegistryService
from app.services.source_attribution import SourceAttributionService
from app.store import store


def reset_store():
    """重置内存仓库到初始seed状态。"""
    from app.seed import SEED_ROWS
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    store._table_data.clear()
    store._versions.clear()
    store._lineage_edges.clear()
    store._quality_results.clear()
    store._snapshot_data.clear()
    store._archive_records.clear()
    store._backfill_partitions.clear()


def _create_ods(ods: OdsLayerService, code: str, source: str, table: str, fields: str,
                batch: str = "RAW-20261101") -> dict:
    entry, missing = ods.create_entry({
        "表编号": code, "来源系统": source, "表名称": table, "分区字段": "dt",
        "存储格式": "PARQUET", "保留天数": "90", "压缩算法": "SNAPPY",
        "创建批次": batch, "字段列表": fields,
    })
    assert entry is not None, missing
    return entry


class TestTrimByAttribution:
    def setup_method(self):
        reset_store()

    def test_trim_exposes_only_owned_fields(self):
        """裁剪后ODS可见字段结构只暴露归属字段清单，越权字段被记录但不进入结构。"""
        ods = OdsLayerService()
        entry = _create_ods(ods, "ODS_T_001", "订单系统", "ods_trim_demo",
                            "order_id,order_amount,evil_field,dt")
        ovs = OdsVisibleSchemaService()
        structure, msg = ovs.generate_for_table(entry)
        assert structure is not None, msg
        assert structure["可见字段清单"] == "order_id,order_amount,dt"
        assert structure["越权字段清单"] == "evil_field"
        assert structure["归属字段清单"] == "order_id,order_amount,order_status,dt"
        assert structure["status"] == "草稿"

    def test_compare_marks_overreach_field(self):
        """详情页比对：越权字段判定为越权裁剪，归属外字段判定为归属未入库。"""
        ods = OdsLayerService()
        entry = _create_ods(ods, "ODS_T_002", "订单系统", "ods_compare_demo",
                            "order_id,evil_field,dt")
        ovs = OdsVisibleSchemaService()
        ovs.generate_for_table(entry)
        compare = {item["字段名称"]: item["判定"] for item in ovs.compare_for_table(entry)}
        assert compare["order_id"] == "保留"
        assert compare["evil_field"] == "越权裁剪"
        assert compare["order_amount"] == "归属未入库"  # 归属有、批次未入库


class TestOnlineJointValidation:
    def setup_method(self):
        reset_store()

    def test_online_blocked_when_overreach_written(self):
        """越权字段没被裁剪却已写入结构时，贴源表不能上线。"""
        ods = OdsLayerService()
        entry = _create_ods(ods, "ODS_T_003", "订单系统", "ods_joint_demo",
                            "order_id,order_amount,dt")
        ovs = OdsVisibleSchemaService()
        structure, _ = ovs.generate_for_table(entry, system=True)
        assert structure is not None
        # 模拟越权字段被写入结构
        structure["可见字段清单"] = "order_id,order_amount,dt,evil_field"
        updated, msg = ods.run_action(entry["id"], "确认上线")
        assert updated is None
        assert "越权字段" in msg
        assert store.find("ods_layer", entry["id"])["status"] != "已上线"

    def test_online_passes_and_corrects_table_fields(self):
        """联合校验通过后，贴源表字段列表被修正为可见字段集合。"""
        ods = OdsLayerService()
        entry = _create_ods(ods, "ODS_T_004", "订单系统", "ods_correct_demo",
                            "order_id,evil_field,dt")
        updated, msg = ods.run_action(entry["id"], "确认上线")
        assert updated is not None, msg
        assert updated["status"] == "已上线"
        # 权限与归属顺着数据流修正贴源表结果
        assert updated["字段列表"] == "order_id,dt"

    def test_online_bootstraps_missing_attribution(self):
        """归属缺失时上线校验自登记归属并生成结构，不静默放行。"""
        ods = OdsLayerService()
        entry = _create_ods(ods, "ODS_T_005", "全新系统", "ods_new_system", "fa,fb")
        updated, msg = ods.run_action(entry["id"], "确认上线")
        assert updated is not None, msg
        attr = SourceAttributionService().get_by_source("全新系统")
        assert attr is not None
        assert attr["归属字段清单"] == "fa,fb"
        structure = OdsVisibleSchemaService().get_by_table("ods_new_system")
        assert structure is not None
        assert structure["可见字段清单"] == "fa,fb"


class TestStatusOneWay:
    def setup_method(self):
        reset_store()

    def _fresh_entry(self) -> dict:
        ods = OdsLayerService()
        entry = _create_ods(ods, "ODS_T_006", "订单系统", "ods_status_demo",
                            "order_id,dt")
        OdsVisibleSchemaService().generate_for_table(entry)
        return entry

    def test_status_only_advances_step_by_step(self):
        """状态只能单向逐级推进：跳级与回退都被拒绝。"""
        ovs = OdsVisibleSchemaService()
        entry = self._fresh_entry()
        # 草稿 → 已生效：跳级拒绝
        structure, msg = ovs.run_action(entry, "标记生效")
        assert structure is None and "跳级" in msg
        # 草稿 → 待校验：允许
        structure, msg = ovs.run_action(entry, "提交校验")
        assert structure is not None and structure["status"] == "待校验"
        # 待校验 → 已废弃：跳级拒绝
        structure, msg = ovs.run_action(entry, "标记废弃")
        assert structure is None and "跳级" in msg
        # 待校验 → 已生效：允许
        structure, msg = ovs.run_action(entry, "标记生效")
        assert structure is not None and structure["status"] == "已生效"
        # 已生效 → 待校验：回退拒绝
        structure, msg = ovs.run_action(entry, "提交校验")
        assert structure is None and "回退" in msg
        # 已生效 → 已废弃：允许
        structure, msg = ovs.run_action(entry, "标记废弃")
        assert structure is not None and structure["status"] == "已废弃"

    def test_activate_rechecks_overreach(self):
        """标记生效前复核归属：归属收紧后残留越权字段不能生效。"""
        ovs = OdsVisibleSchemaService()
        entry = self._fresh_entry()
        ovs.run_action(entry, "提交校验")
        # 归属收紧：订单系统不再拥有 order_amount，并人为把越权字段写进结构
        attr = SourceAttributionService().get_by_source("订单系统")
        attr["归属字段清单"] = "order_id,dt"
        structure = ovs.get_by_table("ods_status_demo")
        structure["可见字段清单"] = "order_id,order_amount,dt"
        updated, msg = ovs.run_action(entry, "标记生效")
        assert updated is None
        assert "越权字段" in msg


class TestPermissionGuard:
    def setup_method(self):
        reset_store()

    def test_controlled_content_cannot_change(self):
        """受控归属：生成、切换、状态推进都被拒绝。"""
        ods = OdsLayerService()
        ovs = OdsVisibleSchemaService()
        pay_entry = ods.get_by_table_name("ods_payments")
        structure, msg = ovs.generate_for_table(pay_entry)
        assert structure is None and "受控" in msg and "不能改动" in msg
        structure, msg = ovs.switch_attribution(pay_entry, "订单系统")
        assert structure is None and "受控" in msg
        structure, msg = ovs.run_action(pay_entry, "标记废弃")
        assert structure is None and "受控" in msg

    def test_shared_content_cannot_change(self):
        """共享归属的内容不能改动。"""
        ods = OdsLayerService()
        entry = _create_ods(ods, "ODS_T_007", "用户系统", "ods_shared_demo",
                            "user_id,user_level,dt", batch="RAW-20261103")
        structure, msg = OdsVisibleSchemaService().generate_for_table(entry)
        assert structure is None and "共享" in msg and "不能改动" in msg

    def test_readonly_content_cannot_change(self):
        """只读归属的内容不能改动。"""
        store.rows("source_attribution").append({
            "id": 90, "status": "已登记", "pending": False, "abnormal": False,
            "归属编号": "ATTR-RO-001", "来源系统": "只读系统",
            "归属字段清单": "f1", "权限级别": "只读", "生效日期": "2026-11-10",
        })
        ods = OdsLayerService()
        entry = _create_ods(ods, "ODS_T_008", "只读系统", "ods_ro_demo", "f1,f2")
        structure, msg = OdsVisibleSchemaService().generate_for_table(entry)
        assert structure is None and "只读" in msg and "不能改动" in msg

    def test_switch_to_unregistered_source_rejected(self):
        """切换到未登记的来源归属属于越权请求，被拒绝。"""
        ods = OdsLayerService()
        entry = ods.get_by_table_name("ods_orders")
        structure, msg = OdsVisibleSchemaService().switch_attribution(entry, "不存在系统")
        assert structure is None
        assert "未登记" in msg and "越权" in msg
        assert ods.get_by_table_name("ods_orders")["来源系统"] == "订单系统"


class TestSwitchAttribution:
    def setup_method(self):
        reset_store()
        store.rows("source_attribution").append({
            "id": 91, "status": "已登记", "pending": False, "abnormal": False,
            "归属编号": "ATTR-ORD2-001", "来源系统": "订单系统V2",
            "归属字段清单": "order_id,dt", "权限级别": "可写", "生效日期": "2026-11-10",
        })

    def test_switch_trims_overreach_everywhere(self):
        """切换后来源归属的越权字段不出现在结构和下游查询中，同源字段完整可读。"""
        ods = OdsLayerService()
        ovs = OdsVisibleSchemaService()
        entry = ods.get_by_table_name("ods_orders")
        store.set_table_rows("ods_orders", [
            {"order_id": "O1", "order_amount": "10", "order_status": "已完成", "dt": "2026-11-01"},
        ])
        structure, msg = ovs.switch_attribution(entry, "订单系统V2")
        assert structure is not None, msg
        assert ods.get_by_table_name("ods_orders")["来源系统"] == "订单系统V2"
        # 结构中越权字段被裁剪
        assert structure["可见字段清单"] == "order_id,dt"
        assert structure["越权字段清单"] == "order_amount,order_status"
        # 下游查询投影：越权字段不出现，同源字段完整
        rows = ods.get_visible_table_data("ods_orders")
        assert rows and set(rows[0].keys()) == {"order_id", "dt"}
        assert rows[0]["order_id"] == "O1"

    def test_switch_back_restores_full_fields(self):
        """切回原归属后，同一来源的完整字段恢复可读。"""
        ods = OdsLayerService()
        ovs = OdsVisibleSchemaService()
        entry = ods.get_by_table_name("ods_orders")
        ovs.switch_attribution(entry, "订单系统V2")
        structure, msg = ovs.switch_attribution(entry, "订单系统")
        assert structure is not None, msg
        assert structure["可见字段清单"] == "order_id,order_amount,order_status,dt"
        assert structure["越权字段清单"] == ""


class TestConflictBoundaries:
    def setup_method(self):
        reset_store()

    def test_strict_boundary_shows_current_fields_with_migration_hint(self):
        """严格边界：ODS结构显示当前系统字段集合，并附迁移提示。"""
        ods = OdsLayerService()
        ovs = OdsVisibleSchemaService()
        entry = ods.get_by_table_name("ods_users")  # 当前用户系统V2，历史批次用户系统
        structure, msg = ovs.generate_for_table(entry, boundary="严格")
        assert structure is not None, msg
        assert structure["冲突标记"] == "是"
        assert structure["可见字段清单"] == "user_id,register_time,dt"
        assert structure["越权字段清单"] == "user_level"
        assert "用户系统" in structure["迁移提示"] and "用户系统V2" in structure["迁移提示"]
        assert "迁移" in structure["迁移提示"]

    def test_relaxed_boundary_shows_historical_fields_with_switch_list(self):
        """放宽边界：ODS结构显示历史批次字段集合，并附来源切换清单。"""
        ods = OdsLayerService()
        ovs = OdsVisibleSchemaService()
        entry = ods.get_by_table_name("ods_users")
        structure, msg = ovs.generate_for_table(entry, boundary="放宽")
        assert structure is not None, msg
        assert structure["冲突标记"] == "是"
        assert structure["可见字段清单"] == "user_id,register_time,user_level,dt"
        assert structure["越权字段清单"] == ""
        assert structure["来源切换清单"] == "用户系统→用户系统V2"
        assert structure["迁移提示"] == ""

    def test_conflict_processing_order_by_batch(self):
        """冲突贴源表按创建批次排处理顺序：批次早的优先处理。"""
        ri = RawIngestService()
        batch, _ = ri.create_entry({
            "批次编号": "RAW-20261001", "来源系统": "订单系统", "文件格式": "CSV",
        })
        assert batch is not None
        ods = OdsLayerService()
        early = _create_ods(ods, "ODS_T_009", "订单系统V3", "ods_conflict_early",
                            "order_id,dt", batch="RAW-20261001")
        ovs = OdsVisibleSchemaService()
        report_early = ovs.conflict_report(early)
        users_entry = ods.get_by_table_name("ods_users")
        report_users = ovs.conflict_report(users_entry)
        # RAW-20261001 早于 RAW-20261103：先处理 ods_conflict_early
        assert report_early["处理顺序"] == "1"
        assert report_early["优先表"] == "ods_conflict_early"
        assert report_users["处理顺序"] == "2"
        assert set(report_users["冲突表清单"].split(",")) == {"ods_conflict_early", "ods_users"}


class TestCrossModuleValidation:
    def setup_method(self):
        reset_store()

    def test_schema_registry_rejects_overreach_fields(self):
        """结构注册跨模块校验：越权字段不能写入贴源表结构。"""
        ovs = OdsVisibleSchemaService()
        violations = ovs.validate_schema_fields("ods_orders", "order_id,evil_field,dt")
        assert violations == ["evil_field"]
        assert ovs.validate_schema_fields("ods_orders", "order_id,dt") == []
        # 非贴源表不约束
        assert ovs.validate_schema_fields("dwd_order_detail", "any_field") == []

    def test_ingest_pipeline_registers_only_visible_fields(self):
        """入库流水线：越权字段经裁剪后才注册结构、落地数据。"""
        ri = RawIngestService()
        entry, _ = ri.create_entry({
            "批次编号": "RAW-20261120", "来源系统": "用户系统", "文件格式": "CSV",
            "原始行数": "5",
        })
        assert entry is not None
        updated, msg = ri.run_action(entry["id"], "确认入库")
        assert updated is not None, msg
        # ods_users 当前归属为用户系统V2（不拥有 user_level），与批次来源用户系统冲突
        # 批次目标字段为 user_id,register_time,user_level（映射规则不含分区字段 dt）
        structure = OdsVisibleSchemaService().get_by_table("ods_users")
        assert structure is not None
        assert structure["可见字段清单"] == "user_id,register_time"
        assert structure["越权字段清单"] == "user_level"
        # 结构注册只含可见字段
        sch = SchemaRegistryService()
        latest = sch.get_latest_schema("ods_users")
        assert "user_level" not in latest["字段名称"]
        # 落地数据也不含越权字段
        rows = store.table_rows("ods_users")
        assert rows and all("user_level" not in row for row in rows)


class TestDownstreamProjection:
    def setup_method(self):
        reset_store()

    def test_projection_hides_overreach_fields(self):
        """下游查询：越权字段不出现，归属内字段完整可读。"""
        store.set_table_rows("ods_users", [
            {"user_id": "U1", "register_time": "2026-10-01", "user_level": "VIP", "dt": "2026-11-03"},
        ])
        ods = OdsLayerService()
        rows = ods.get_visible_table_data("ods_users")
        assert len(rows) == 1
        assert "user_level" not in rows[0]
        assert rows[0]["user_id"] == "U1"
        assert rows[0]["register_time"] == "2026-10-01"
        assert rows[0]["dt"] == "2026-11-03"
