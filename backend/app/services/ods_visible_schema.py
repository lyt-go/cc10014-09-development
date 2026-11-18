"""ODS可见字段结构业务规则：按来源归属裁剪入库批次字段结构。

核心规则：
- 贴源表入库批次的字段结构按来源归属裁剪，生成的ODS可见字段结构只暴露
  归属字段清单内的字段；越权字段记录但不进入结构，下游查询同样读不到。
- 可见字段结构状态只能单向逐级推进（草稿→待校验→已生效→已废弃），
  权限与归属校验拒绝跳级与回退。
- 来源归属权限为只读、受控、共享时，结构内容不能改动，越权请求被拒绝。
- 来源系统名称变更与历史批次归属冲突时：严格边界按当前系统字段集合裁剪
  并附迁移提示；放宽边界保留历史批次字段集合并附来源切换清单。冲突的
  贴源表按创建批次排处理顺序，批次最早的优先处理。
"""
from __future__ import annotations

from typing import Any

from app.services.source_attribution import SourceAttributionService, split_fields
from app.store import store

MODULE = "ods_visible_schema"
ODS_MODULE = "ods_layer"
LIST_FIELDS = [
    "结构编号", "表名称", "来源系统", "批次字段清单", "归属字段清单",
    "可见字段清单", "越权字段清单", "边界模式", "冲突标记", "迁移提示",
    "来源切换清单", "处理顺序",
]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
BOUNDARIES = ["严格", "放宽"]

attr_service = SourceAttributionService()


class OdsVisibleSchemaService:
    # ---- 基础查询 ----

    def get_by_table(self, table_name: str) -> dict[str, Any] | None:
        return store.find_by_field(MODULE, "表名称", table_name)

    def _historical_source(self, ods_entry: dict[str, Any]) -> str:
        """创建批次上记录的来源系统，即历史批次归属。"""
        batch_no = str(ods_entry.get("创建批次", "") or "")
        if not batch_no:
            return ""
        batch = store.find_by_field("raw_ingest", "批次编号", batch_no)
        return str(batch.get("来源系统", "")) if batch else ""

    def _has_conflict(self, ods_entry: dict[str, Any]) -> bool:
        historical = self._historical_source(ods_entry)
        current = str(ods_entry.get("来源系统", "") or "")
        return bool(historical and current and historical != current)

    def _effective_source(self, ods_entry: dict[str, Any], boundary: str) -> tuple[str, bool, str]:
        """按边界模式选定生效归属：严格取当前系统，放宽取历史批次归属。"""
        current = str(ods_entry.get("来源系统", "") or "")
        historical = self._historical_source(ods_entry)
        conflict = bool(historical and current and historical != current)
        if conflict and boundary == "放宽":
            return historical, conflict, historical
        return current, conflict, historical

    # ---- 裁剪生成 ----

    def generate_for_table(
        self,
        ods_entry: dict[str, Any],
        *,
        boundary: str = "严格",
        system: bool = False,
        batch_fields: list[str] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        """按来源归属裁剪入库批次字段结构，生成/更新ODS可见字段结构。

        system=True 表示入库流水线自动裁剪，不占用人工改动权限；
        人工生成/重裁剪时，只读、受控、共享的归属内容不能改动。
        """
        if boundary not in BOUNDARIES:
            return None, f"边界模式「{boundary}」不支持，只能是：{'、'.join(BOUNDARIES)}"
        table_name = str(ods_entry.get("表名称", "") or "")
        current_source = str(ods_entry.get("来源系统", "") or "")
        if not table_name or not current_source:
            return None, "贴源表缺少表名称或来源系统，无法裁剪"
        if not system and not attr_service.is_mutable(current_source):
            permission = attr_service.permission_of(current_source)
            return None, f"来源归属「{current_source}」权限级别为「{permission}」，只读、受控和共享内容不能改动"

        existing = self.get_by_table(table_name)
        if batch_fields is None:
            # 重裁剪以结构中留存的批次字段为准；首次生成取贴源表当前字段列表
            stored = split_fields(existing.get("批次字段清单")) if existing else []
            batch_fields = stored or split_fields(ods_entry.get("字段列表"))

        effective_source, conflict, historical = self._effective_source(ods_entry, boundary)
        attribution = attr_service.ensure_attribution(effective_source, batch_fields)
        owned = split_fields(attribution.get("归属字段清单"))
        visible = [field for field in batch_fields if field in owned]
        overreach = [field for field in batch_fields if field not in owned]

        migration_hint = ""
        switch_list = ""
        if conflict and boundary == "严格":
            migration_hint = f"来源系统由「{historical}」变更为「{current_source}」：按当前归属裁剪"
            if overreach:
                migration_hint += f"，越权字段 {'、'.join(overreach)} 已移除"
            migration_hint += "，历史批次字段需迁移"
        elif conflict and boundary == "放宽":
            switch_list = f"{historical}→{current_source}"

        values = {
            "表名称": table_name,
            "来源系统": current_source,
            "批次字段清单": ",".join(batch_fields),
            "归属字段清单": ",".join(owned),
            "可见字段清单": ",".join(visible),
            "越权字段清单": ",".join(overreach),
            "边界模式": boundary,
            "冲突标记": "是" if conflict else "否",
            "迁移提示": migration_hint,
            "来源切换清单": switch_list,
            "处理顺序": self._processing_rank(ods_entry),
        }
        if existing is None:
            rows = store.rows(MODULE)
            existing = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            existing["结构编号"] = f"VIS-{ods_entry.get('表编号', table_name)}"
            existing["status"] = STATUS_ORDER[0]
            existing["abnormal"] = False
            rows.append(existing)
        existing.update(values)
        existing["pending"] = existing["status"] in STATUS_ORDER[:2]
        store.save_version(MODULE, int(existing["id"]), existing)
        message = f"ODS可见字段结构已按「{boundary}」边界裁剪：可见{len(visible)}个字段"
        if overreach:
            message += f"，裁剪越权字段 {'、'.join(overreach)}"
        return existing, message

    def _processing_rank(self, ods_entry: dict[str, Any]) -> str:
        """冲突贴源表按创建批次（再按表编号）排序，返回本表的处理顺序（1起）。"""
        if not self._has_conflict(ods_entry):
            return ""
        conflicting = [row for row in store.rows(ODS_MODULE) if self._has_conflict(row)]
        conflicting.sort(key=lambda row: (str(row.get("创建批次", "")), str(row.get("表编号", ""))))
        for index, row in enumerate(conflicting, start=1):
            if row.get("表名称") == ods_entry.get("表名称"):
                return str(index)
        return ""

    def conflict_report(self, ods_entry: dict[str, Any]) -> dict[str, Any]:
        """冲突详情：谁优先、哪个先处理，按创建批次升序给出处理顺序。"""
        historical = self._historical_source(ods_entry)
        current = str(ods_entry.get("来源系统", "") or "")
        conflict = bool(historical and current and historical != current)
        conflicting = [row for row in store.rows(ODS_MODULE) if self._has_conflict(row)]
        conflicting.sort(key=lambda row: (str(row.get("创建批次", "")), str(row.get("表编号", ""))))
        order = [str(row.get("表名称", "")) for row in conflicting]
        rank = ""
        if conflict and ods_entry.get("表名称") in order:
            rank = str(order.index(ods_entry.get("表名称")) + 1)
        return {
            "冲突标记": "是" if conflict else "否",
            "历史来源": historical,
            "当前来源": current,
            "处理顺序": rank,
            "优先表": order[0] if order else "",
            "冲突表清单": ",".join(order),
        }

    # ---- 状态流转：单向逐级，不允许跳级或回退 ----

    def run_action(self, ods_entry: dict[str, Any], action: str) -> tuple[dict[str, Any] | None, str]:
        table_name = str(ods_entry.get("表名称", "") or "")
        structure = self.get_by_table(table_name)
        if structure is None:
            return None, "ODS可见字段结构尚未生成，请先生成"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于ODS可见字段结构可执行范围"
        source = str(structure.get("来源系统", "") or "")
        if not attr_service.is_mutable(source):
            permission = attr_service.permission_of(source)
            return None, f"来源归属「{source}」权限级别为「{permission}」，只读、受控和共享内容不能改动"
        current = str(structure.get("status", STATUS_ORDER[0]))
        target = ACTION_RULES[action]
        current_idx = STATUS_ORDER.index(current) if current in STATUS_ORDER else 0
        target_idx = STATUS_ORDER.index(target)
        if target_idx != current_idx + 1:
            direction = "回退" if target_idx <= current_idx else "跳级"
            return None, f"状态只能单向逐级推进：当前「{current}」不能{direction}到「{target}」"
        if action == "标记生效":
            # 生效前复核：越权字段没被裁剪却已写入结构时拒绝生效
            violations = self._online_violations(structure)
            if violations:
                return None, f"越权字段未裁剪却已写入结构：{'、'.join(violations)}，不能生效"
        structure["status"] = target
        structure["pending"] = target in STATUS_ORDER[:2]
        store.save_version(MODULE, int(structure["id"]), structure)
        return structure, f"ODS可见字段结构已{action}"

    def _online_violations(self, structure: dict[str, Any]) -> list[str]:
        """可见字段相对生效归属的越权项（归属收紧后会暴露出来）。"""
        ods_entry = store.find_by_field(ODS_MODULE, "表名称", str(structure.get("表名称", "")))
        boundary = str(structure.get("边界模式", "严格"))
        if ods_entry is not None:
            effective_source, _, _ = self._effective_source(ods_entry, boundary)
        else:
            effective_source = str(structure.get("来源系统", ""))
        owned = attr_service.owned_fields(effective_source)
        return [field for field in split_fields(structure.get("可见字段清单")) if field not in owned]

    # ---- 切换来源归属 ----

    def switch_attribution(
        self,
        ods_entry: dict[str, Any],
        new_source: str,
        *,
        boundary: str = "严格",
    ) -> tuple[dict[str, Any] | None, str]:
        """切换贴源表的来源归属并按新归属重新裁剪。

        切换后越权字段不出现在结构和下游查询中，同一来源的完整字段仍可读取。
        """
        new_source = (new_source or "").strip()
        current_source = str(ods_entry.get("来源系统", "") or "")
        if not new_source:
            return None, "目标来源归属不能为空"
        if new_source == current_source:
            return None, f"贴源表已归属「{new_source}」，无需切换"
        if not attr_service.is_mutable(current_source):
            permission = attr_service.permission_of(current_source)
            return None, f"来源归属「{current_source}」权限级别为「{permission}」，只读、受控和共享内容不能改动"
        if attr_service.get_by_source(new_source) is None:
            return None, f"目标来源归属「{new_source}」未登记，越权请求被拒绝"
        ods_entry["来源系统"] = new_source
        store.save_version(ODS_MODULE, int(ods_entry["id"]), ods_entry)
        structure, message = self.generate_for_table(ods_entry, boundary=boundary, system=True)
        if structure is None:
            return None, message
        return structure, f"来源归属已切换：{current_source}→{new_source}；{message}"

    # ---- 上线联合校验 ----

    def validate_online(self, ods_entry: dict[str, Any]) -> tuple[bool, str]:
        """来源归属和贴源表上线一起校验：归属缺失先自登记，结构缺失先裁剪，
        越权字段没被裁剪却已写入结构时拒绝上线；通过后顺着数据流把贴源表
        字段列表修正为可见字段集合。"""
        table_name = str(ods_entry.get("表名称", "") or "")
        source = str(ods_entry.get("来源系统", "") or "")
        attr_service.ensure_attribution(source, split_fields(ods_entry.get("字段列表")))
        structure = self.get_by_table(table_name)
        if structure is None:
            structure, message = self.generate_for_table(ods_entry, system=True)
            if structure is None:
                return False, message
        violations = self._online_violations(structure)
        if violations:
            return False, f"越权字段未裁剪却已写入结构：{'、'.join(violations)}，贴源表不能上线"
        visible_text = str(structure.get("可见字段清单", ""))
        if str(ods_entry.get("字段列表", "") or "") != visible_text:
            ods_entry["字段列表"] = visible_text
        return True, "来源归属与贴源表联合校验通过"

    # ---- 跨模块校验与下游投影 ----

    def validate_schema_fields(self, table_name: str, fields_text: str) -> list[str]:
        """结构注册跨模块校验：注册到贴源表的字段必须落在可见字段结构内，
        返回越权字段清单（空列表表示通过）。非贴源表不约束。"""
        ods_entry = store.find_by_field(ODS_MODULE, "表名称", table_name)
        if ods_entry is None:
            return []
        structure = self.get_by_table(table_name)
        if structure is None:
            structure, _ = self.generate_for_table(ods_entry, system=True)
        if structure is None:
            return []
        visible = set(split_fields(structure.get("可见字段清单")))
        return [field for field in split_fields(fields_text) if field not in visible]

    def project_rows(self, table_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """下游查询裁剪：只投影可见字段，越权字段不出现在查询结果中。"""
        structure = self.get_by_table(table_name)
        if structure is None:
            return rows
        visible = split_fields(structure.get("可见字段清单"))
        keep = set(visible)
        return [{key: value for key, value in row.items() if key in keep} for row in rows]

    # ---- 详情页比对 ----

    def compare_for_table(self, ods_entry: dict[str, Any]) -> list[dict[str, Any]]:
        """比对贴源表结果：入库批次字段、归属字段、ODS可见字段逐字段对照。"""
        table_name = str(ods_entry.get("表名称", "") or "")
        structure = self.get_by_table(table_name)
        if structure is not None:
            batch_fields = split_fields(structure.get("批次字段清单"))
            owned = split_fields(structure.get("归属字段清单"))
            visible = split_fields(structure.get("可见字段清单"))
        else:
            batch_fields = split_fields(ods_entry.get("字段列表"))
            owned = attr_service.owned_fields(str(ods_entry.get("来源系统", "")))
            visible = []
        ordered: list[str] = []
        for field in [*batch_fields, *owned, *visible]:
            if field not in ordered:
                ordered.append(field)
        items: list[dict[str, Any]] = []
        for field in ordered:
            in_batch = field in batch_fields
            in_owned = field in owned
            in_visible = field in visible
            if in_batch and in_owned and in_visible:
                verdict = "保留"
            elif in_batch and not in_owned:
                verdict = "越权裁剪"
            elif not in_batch and in_owned:
                verdict = "归属未入库"
            elif in_visible and not in_batch:
                verdict = "异常回写"
            else:
                verdict = "待裁剪"
            items.append({
                "字段名称": field,
                "批次字段": "是" if in_batch else "否",
                "归属字段": "是" if in_owned else "否",
                "ODS可见": "是" if in_visible else "否",
                "判定": verdict,
            })
        return items
