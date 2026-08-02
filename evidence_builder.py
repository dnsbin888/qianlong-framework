"""
Evidence Builder — ML Evidence Producer (Phase 1 P0-1)
========================================================
将现有 ML 信号封装为标准化 Evidence 对象。
不改模型、不改分数、不改决策。

架构:
  TrendML (LGBM)  → build_evidence()  → Evidence Object
  MomentumML (XGB) → build_evidence()  → Evidence Object

Gate 1:
  - Decision Equivalence: 新旧 Replay Binary Compare = 100%
  - Every Signal → Exactly One Evidence (不丢不重)

Feature Toggle:
  ENABLE_EVIDENCE = False → 整个 Evidence 体系关闭，系统恢复今天状态
"""
import json
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from pathlib import Path

# ── Feature Toggle: 关闭后系统恢复原样 ──
ENABLE_EVIDENCE = True

# ── 从 Registry 读取 Producer 身份（不 import AQF-T 模块，直接读 JSON） ──
REGISTRY_PATH = r"D:\AQF-T\contracts\evidence_registry.json"


@dataclass
class Evidence:
    """Evidence Object — Phase 1 P0-1/2 标准输出"""

    # ── P0-2: 三层身份 ──
    human_id: str                        # AQFT-20260802-000001
    machine_id: str                      # UUID v7
    evidence_seq: int                    # Producer 今日序号
    producer_id: str                     # "trend_ml" | "momentum_ml"
    evidence_type: str                   # "trend_evidence" | "momentum_evidence"

    # ── 时间 ──
    timestamp: str                       # ISO8601

    # ── 标的 ──
    symbol: str

    # ── 信号（不改动原有字段语义） ──
    score: float                         # 模型原始评分
    buy_signal: int                      # 信号等级 1-5
    close: float                         # 最新收盘价
    confidence: float                    # 置信度 0-1

    # ── 快照（可审计性：还原当时状态） ──
    snapshot: dict = field(default_factory=dict)

    # ── 元数据 ──
    metadata: dict = field(default_factory=dict)

    # ── 预留 ──
    parent_evidence_id: Optional[str] = None  # Evidence Graph

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════
# Evidence Builder
# ═══════════════════════════════════════════════════════

class EvidenceBuilder:
    """ML 信号的 Evidence 封装器

    用法:
        builder = EvidenceBuilder()
        evidence = builder.build(signal, producer_id="trend_ml")
    """

    def __init__(self, registry_path: str = None):
        self._registry_path = registry_path or REGISTRY_PATH
        self._registry: dict = {}
        self._load_registry()
        from evidence_id import EvidenceIdGenerator
        self._id_gen = EvidenceIdGenerator()
        self._counter: int = 0

    def _load_registry(self):
        try:
            with open(self._registry_path, "r", encoding="utf-8") as f:
                self._registry = json.load(f)
        except Exception:
            self._registry = {}

    def _get_producer_meta(self, producer_id: str) -> dict:
        return self._registry.get("producers", {}).get(producer_id, {})

    def build(self, signal: dict, producer_id: str) -> Optional[Evidence]:
        if not ENABLE_EVIDENCE:
            return None

        meta = self._get_producer_meta(producer_id)
        if not meta or meta.get("lifecycle") != "ACTIVE" or not meta.get("enabled"):
            return None

        self._counter += 1

        # ── P0-2: 生成 EvidenceId ──
        eid = self._id_gen.generate(producer_id)

        score = signal.get("score", 0)
        confidence = self._estimate_confidence(score)

        # ── 快照 ──
        impl = meta.get("implementation", {})
        snapshot = {
            "algorithm": impl.get("algorithm", ""),
            "algorithm_version": impl.get("version", ""),
            "model_file": impl.get("model_file", ""),
            "original_score": score,
            "original_buy_signal": signal.get("buy_signal", 0),
        }

        return Evidence(
            human_id=eid.human_id,
            machine_id=eid.machine_id,
            evidence_seq=eid.evidence_seq,
            parent_evidence_id=eid.parent_evidence_id,
            producer_id=producer_id,
            evidence_type=meta.get("evidence_type", ""),
            timestamp=datetime.now().isoformat(),
            symbol=signal.get("symbol", ""),
            score=score,
            buy_signal=signal.get("buy_signal", 0),
            close=signal.get("close", 0.0),
            confidence=round(confidence, 4),
            snapshot=snapshot,
            metadata={
                "display_name": meta.get("display_name", ""),
                "contract_version": meta.get("contract_version", ""),
                "owner": meta.get("owner", ""),
                "strategy": signal.get("strategy", ""),
            },
        )

    def build_batch(self, signals: list[dict], producer_id: str) -> list[Evidence]:
        """批量封装。不丢不重——输入 N 条 → 输出 N 条 Evidence。"""
        return [e for s in signals if (e := self.build(s, producer_id)) is not None]

    # ══════════════════════════════════════════════════
    # 辅助
    # ══════════════════════════════════════════════════

    @staticmethod
    def _estimate_confidence(score: float) -> float:
        """不改模型输出的置信度估算。Phase 1 映射表，P0-3 用真实数据校准。"""
        if score >= 90:
            return 0.85
        elif score >= 80:
            return 0.78
        elif score >= 70:
            return 0.70
        elif score >= 60:
            return 0.60
        elif score >= 50:
            return 0.50
        elif score >= 40:
            return 0.40
        return 0.30

    @property
    def counter(self) -> int:
        return self._counter


# ═══════════════════════════════════════════════════════
# Gate 1 验证
# ═══════════════════════════════════════════════════════

def gate1_verify(original_signals: list[dict], evidence_list: list[Evidence],
                 producer_id: str) -> dict:
    """
    Gate 1: Decision Equivalence + Evidence Count Match

    验证:
      1. Evidence 数量 == 信号数量（不丢不重）
      2. 每个 Evidence 的 buy_signal == 原始信号的 buy_signal（决策不变）
      3. 每个 Evidence 的 score == 原始信号的 score（分数不变）
    """
    n_sig = len(original_signals)
    n_ev = len(evidence_list)

    mismatches = []
    for i, (sig, ev) in enumerate(zip(original_signals, evidence_list)):
        if sig["buy_signal"] != ev.buy_signal:
            mismatches.append({
                "index": i,
                "symbol": sig["symbol"],
                "original_buy_signal": sig["buy_signal"],
                "evidence_buy_signal": ev.buy_signal,
            })
        if abs(sig["score"] - ev.score) > 0.01:
            mismatches.append({
                "index": i,
                "symbol": sig["symbol"],
                "original_score": sig["score"],
                "evidence_score": ev.score,
            })

    result = {
        "producer": producer_id,
        "signal_count": n_sig,
        "evidence_count": n_ev,
        "count_match": n_sig == n_ev,
        "decision_identical": len(mismatches) == 0,
        "mismatches": mismatches,
        "gate1_passed": (n_sig == n_ev) and (len(mismatches) == 0) and (n_sig > 0),
    }
    return result


# ═══════════════════════════════════════════════════════
# CLI: 验证 Evidence Builder
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("  Evidence Builder — Gate 1 自检")
    print("=" * 60)

    builder = EvidenceBuilder()

    # 模拟信号（不改真实 ML 输出的字段）
    test_signals = [
        {"symbol": "000001", "buy_signal": 5, "close": 12.50, "score": 92.0, "name": "平安银行", "strategy": "LightGBM-v1"},
        {"symbol": "000002", "buy_signal": 4, "close": 15.10, "score": 78.0, "name": "万科A", "strategy": "LightGBM-v1"},
        {"symbol": "000858", "buy_signal": 3, "close": 160.0, "score": 65.0, "name": "五粮液", "strategy": "LightGBM-v1"},
    ]

    # TrendML
    evidences = builder.build_batch(test_signals, "trend_ml")
    result = gate1_verify(test_signals, evidences, "trend_ml")

    print(f"\n  Signals:  {result['signal_count']}")
    print(f"  Evidence: {result['evidence_count']}")
    print(f"  Count Match:       {'✅' if result['count_match'] else '❌'}")
    print(f"  Decision Identical: {'✅' if result['decision_identical'] else '❌'}")
    print(f"  GATE 1:            {'✅ PASSED' if result['gate1_passed'] else '❌ FAILED'}")

    if result["mismatches"]:
        print(f"\n  Mismatches: {len(result['mismatches'])}")
        for m in result["mismatches"]:
            print(f"    ❌ {m}")

    print(f"\n  Sample Evidence:")
    print(f"  {evidences[0].to_json()}")
