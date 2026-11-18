"""来源归属业务规则：维护来源系统的归属字段清单与权限级别。

贴源层按来源归属裁剪字段：每个来源系统登记一条归属，声明它拥有的字段
（归属字段清单）和权限级别。权限级别为只读、受控、共享时，归属内容及其
裁剪出的ODS可见字段结构不允许改动；可写才允许生成、重裁剪与切换归属。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "source_attribution"
LIST_FIELDS = ["归属编号", "来源系统", "归属字段清单", "权限级别", "生效日期"]
REQUIRED_FIELDS = ["归属编号", "来源系统", "归属字段清单"]
# 只读、受控、共享内容不能改动；只有可写归属允许变更可见字段结构
MUTABLE_PERMISSION = "可写"
READONLY_PERMISSIONS = ["只读", "受控", "共享"]
PERMISSIONS = [MUTABLE_PERMISSION, *READONLY_PERMISSIONS]


def split_fields(text: Any) -> list[str]:
    """把逗号分隔的字段清单拆成有序列表，忽略空项。"""
    return [part.strip() for part in str(text or "").split(",") if part.strip()]


class SourceAttributionService:
    def list_attributions(self) -> list[dict[str, Any]]:
        return store.rows(MODULE)

    def get_by_source(self, source: str) -> dict[str, Any] | None:
        if not source:
            return None
        return store.find_by_field(MODULE, "来源系统", source)

    def owned_fields(self, source: str) -> list[str]:
        """来源系统声明拥有的字段；未登记时返回空列表。"""
        attribution = self.get_by_source(source)
        if not attribution:
            return []
        return split_fields(attribution.get("归属字段清单"))

    def permission_of(self, source: str) -> str:
        attribution = self.get_by_source(source)
        return str(attribution.get("权限级别", "")) if attribution else ""

    def is_mutable(self, source: str) -> bool:
        """只读、受控、共享内容不能改动；未登记的归属视为可写（首次自登记）。"""
        attribution = self.get_by_source(source)
        if attribution is None:
            return True
        return str(attribution.get("权限级别", "")) == MUTABLE_PERMISSION

    def ensure_attribution(self, source: str, fields: list[str]) -> dict[str, Any]:
        """归属缺失时按给定字段自登记（可写），保证上线校验总有归属可比对。"""
        attribution = self.get_by_source(source)
        if attribution is not None:
            return attribution
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({
            "归属编号": f"ATTR-AUTO-{len(rows) + 1:03d}",
            "来源系统": source,
            "归属字段清单": ",".join(fields),
            "权限级别": MUTABLE_PERMISSION,
            "生效日期": "",
        })
        entry["status"] = "已登记"
        entry["pending"] = False
        entry["abnormal"] = False
        rows.append(entry)
        return entry
