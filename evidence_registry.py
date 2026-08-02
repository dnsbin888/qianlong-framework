"""
Evidence Registry — 整个交易平台唯一的 Producer 元数据来源
============================================================
Single Source of Truth for Evidence Producers.
任何新的 Evidence Producer 必须先注册，再实现。

Phase 1 P0-0: 根对象 (Root Object)，非简单配置文件

用法:
    from evidence_registry import EvidenceRegistry

    reg = EvidenceRegistry()
    reg.load()              # 加载 JSON
    reg.validate()          # 校验格式
    reg.get("trend_ml")     # 获取单个 Producer
    reg.active_producers()  # 获取所有 ACTIVE 的 Producer
    reg.summary()           # 打印注册表摘要
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Registry 文件路径 ──
REGISTRY_PATH = r"D:\AQF-T\contracts\evidence_registry.json"

# ── 合法的 lifecycle 状态 ──
VALID_LIFECYCLES = {"ACTIVE", "DEPRECATED", "ARCHIVED"}

# ── 合法的 evidence_type ──
VALID_EVIDENCE_TYPES = {
    "trend_evidence", "momentum_evidence", "ml_signal",
    "formula_evidence", "board_evidence", "regime_evidence",
    "exit_evidence"
}

# ── 必填字段 ──
REQUIRED_FIELDS = [
    "producer_id", "display_name", "evidence_type",
    "responsibility", "implementation", "contract_version",
    "owner", "lifecycle"
]

# ── Feature Toggle: 关闭后整个 Evidence 体系不生效 ──
ENABLE_EVIDENCE = True


class EvidenceRegistry:
    """Producer 注册表 — 整个 Evidence 体系的根对象"""

    def __init__(self, path: str = None):
        self._path = path or REGISTRY_PATH
        self._data: dict = {}
        self._loaded: bool = False
        self._errors: list[str] = []
        self._warnings: list[str] = []

    # ═══════════════════════════════════════════════════════
    # Load / Save
    # ═══════════════════════════════════════════════════════

    def load(self) -> bool:
        """从 JSON 加载注册表。返回 True 如果成功。"""
        if not os.path.exists(self._path):
            self._errors.append(f"Registry file not found: {self._path}")
            return False
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._loaded = True
            return True
        except json.JSONDecodeError as e:
            self._errors.append(f"JSON parse error: {e}")
            return False

    def reload(self) -> bool:
        """重新加载（热更新）"""
        self._data = {}
        self._errors = []
        self._warnings = []
        self._loaded = False
        return self.load()

    # ═══════════════════════════════════════════════════════
    # Validate
    # ═══════════════════════════════════════════════════════

    def validate(self) -> tuple[bool, list[str], list[str]]:
        """
        校验注册表格式。
        Returns: (is_valid, errors, warnings)
        """
        self._errors = []
        self._warnings = []

        if not self._loaded:
            self._errors.append("Registry not loaded. Call load() first.")
            return False, self._errors, self._warnings

        producers = self._data.get("producers", {})
        if not producers:
            self._errors.append("No producers defined in registry.")
            return False, self._errors, self._warnings

        seen_ids = set()
        seen_types = {}

        for pid, p in producers.items():
            # 1. producer_id 是否与 key 一致
            if p.get("producer_id") != pid:
                self._errors.append(f"[{pid}] producer_id mismatch: key={pid}, value={p.get('producer_id')}")

            # 2. producer_id 是否唯一
            if pid in seen_ids:
                self._errors.append(f"[{pid}] Duplicate producer_id")
            seen_ids.add(pid)

            # 3. 必填字段
            for field in REQUIRED_FIELDS:
                if field not in p or p[field] is None or p[field] == "":
                    self._errors.append(f"[{pid}] Missing required field: {field}")

            # 4. lifecycle 是否合法
            lifecycle = p.get("lifecycle", "")
            if lifecycle not in VALID_LIFECYCLES:
                self._errors.append(f"[{pid}] Invalid lifecycle: '{lifecycle}'. Must be one of {VALID_LIFECYCLES}")

            # 5. evidence_type 是否合法
            etype = p.get("evidence_type", "")
            if etype and etype not in VALID_EVIDENCE_TYPES:
                self._warnings.append(f"[{pid}] Unknown evidence_type: '{etype}'. Consider adding to VALID_EVIDENCE_TYPES")

            # 6. implementation 完整性
            impl = p.get("implementation", {})
            if not impl.get("algorithm"):
                self._errors.append(f"[{pid}] implementation.algorithm is required")
            if not impl.get("version"):
                self._warnings.append(f"[{pid}] implementation.version is empty")

            # 7. ARCHIVED 必须有 archived_reason
            if lifecycle == "ARCHIVED" and not p.get("archived_reason"):
                self._warnings.append(f"[{pid}] ARCHIVED without archived_reason")

            # 8. enabled=true 但 lifecycle!=ACTIVE
            if p.get("enabled") and lifecycle != "ACTIVE":
                self._warnings.append(f"[{pid}] enabled=true but lifecycle={lifecycle}")

        is_valid = len(self._errors) == 0
        return is_valid, self._errors, self._warnings

    # ═══════════════════════════════════════════════════════
    # Query
    # ═══════════════════════════════════════════════════════

    def get(self, producer_id: str) -> Optional[dict]:
        """获取单个 Producer"""
        return self._data.get("producers", {}).get(producer_id)

    def active_producers(self) -> list[dict]:
        """获取所有 ACTIVE 的 Producer"""
        return [
            p for p in self._data.get("producers", {}).values()
            if p.get("lifecycle") == "ACTIVE"
        ]

    def enabled_producers(self) -> list[dict]:
        """获取所有 enabled 的 Producer"""
        return [
            p for p in self._data.get("producers", {}).values()
            if p.get("enabled") and p.get("lifecycle") == "ACTIVE"
        ]

    def producers_by_owner(self, owner: str) -> list[dict]:
        """按 owner 筛选 Producer"""
        return [
            p for p in self._data.get("producers", {}).values()
            if p.get("owner") == owner
        ]

    def producers_by_type(self, evidence_type: str) -> list[dict]:
        """按 evidence_type 筛选"""
        return [
            p for p in self._data.get("producers", {}).values()
            if p.get("evidence_type") == evidence_type
        ]

    def all_producers(self) -> dict:
        """返回全部 Producer (含 ARCHIVED)"""
        return self._data.get("producers", {})

    # ═══════════════════════════════════════════════════════
    # Utility
    # ═══════════════════════════════════════════════════════

    def is_enabled(self, producer_id: str) -> bool:
        """检查 Producer 是否可用"""
        if not ENABLE_EVIDENCE:
            return False
        p = self.get(producer_id)
        if not p:
            return False
        return p.get("enabled", False) and p.get("lifecycle") == "ACTIVE"

    def summary(self) -> str:
        """打印注册表摘要"""
        if not self._loaded:
            return "Registry not loaded."

        producers = self._data.get("producers", {})
        lines = [
            "=" * 65,
            f"  Evidence Registry v{self._data.get('_meta', {}).get('registry_version', '?')}",
            f"  {len(producers)} Producers registered",
            "=" * 65,
        ]
        for pid, p in producers.items():
            lc = p.get("lifecycle", "?")
            enabled = "✓" if p.get("enabled") else "✗"
            icon = {"ACTIVE": "●", "DEPRECATED": "○", "ARCHIVED": "⊗"}.get(lc, "?")
            lines.append(
                f"  {icon} {pid:20s}  {enabled}  {lc:10s}  {p.get('display_name', '')}"
            )
        lines.append("=" * 65)
        return "\n".join(lines)

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def producer_count(self) -> int:
        return len(self._data.get("producers", {}))


# ═══════════════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════════════

def load_registry() -> EvidenceRegistry:
    """加载 + 校验，打印结果。一行调用。"""
    reg = EvidenceRegistry()
    if not reg.load():
        print("[Registry] FAILED TO LOAD")
        return reg

    valid, errors, warnings = reg.validate()
    if errors:
        print(f"[Registry] VALIDATION FAILED: {len(errors)} errors")
        for e in errors:
            print(f"  ❌ {e}")
    if warnings:
        for w in warnings:
            print(f"  ⚠️ {w}")
    if valid:
        print(f"[Registry] OK — {reg.producer_count} producers, {len(reg.active_producers())} active")
    return reg


# ═══════════════════════════════════════════════════════
# CLI: python evidence_registry.py → 验证 + 摘要
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    reg = load_registry()
    if reg.loaded:
        print(reg.summary())
