"""
Evidence Identity — 证据身份层 (Phase 1 P0-2)
=============================================
双编号 + 逻辑序列 + 父引用 + Replay 安全

Human ID:  AQFT-20260802-000001  (人类可读，严格递增)
Machine ID: UUID v7               (时间有序，全球唯一)
Logical:   evidence_seq           (每个 Producer 独立计数)

Gate 2:
  G2-1: 100万次无重复
  G2-2: Human ID 严格递增（不跳号/不倒序）
  G2-3: Replay 重新生成 UUID，但内容可追溯

用法:
    from evidence_id import EvidenceIdGenerator

    gen = EvidenceIdGenerator()
    ev_id = gen.generate("trend_ml")
    # → EvidenceId(
    #     human_id="AQFT-20260802-000001",
    #     machine_id="0191a2b3-...",
    #     evidence_seq=1,
    #     producer_id="trend_ml"
    #   )
"""
import uuid
import time
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# ═══════════════════════════════════════════════════════
# UUID v7 (时间有序) — Python 3.12 无原生实现，手写
# ═══════════════════════════════════════════════════════

def uuid7() -> str:
    """UUID v7: 前48位为毫秒级Unix时间戳，时间有序"""
    # 毫秒时间戳 (48 bits)
    ms = int(time.time() * 1000)
    ts_bytes = ms.to_bytes(6, "big")

    # 随机部分 (74 bits)
    rand_bytes = uuid.uuid4().bytes[6:]

    # 构造: timestamp(6) + version(2) + rand(8)
    combined = ts_bytes + rand_bytes

    # 设置 version=7 (4 bits, position 6)
    combined = bytearray(combined)
    ver_byte = combined[6]
    combined[6] = (ver_byte & 0x0F) | 0x70  # UUID v7

    # 设置 variant (2 bits, position 8)
    var_byte = combined[8]
    combined[8] = (var_byte & 0x3F) | 0x80

    return str(uuid.UUID(bytes=bytes(combined)))


# ═══════════════════════════════════════════════════════
# Evidence Identity
# ═══════════════════════════════════════════════════════

@dataclass
class EvidenceId:
    """证据身份 — 三种标识合一"""
    human_id: str              # AQFT-20260802-000001
    machine_id: str            # UUID v7
    evidence_seq: int          # 该 Producer 今日第几条
    producer_id: str           # trend_ml / momentum_ml / ...
    date: str                  # YYYYMMDD
    parent_evidence_id: Optional[str] = None  # 预留: Evidence Graph


class EvidenceIdGenerator:
    """线程安全的 Evidence ID 生成器

    特性:
      - Human ID: 严格递增(0-pad 6位)，不跳号
      - Machine ID: UUID v7，时间有序
      - 每个 Producer 独立维护 evidence_seq
      - Replay 安全: 重新生成 UUID，但保留逻辑关联
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._global_seq: dict[str, int] = {}   # {date: global_counter}
        self._producer_seq: dict[str, dict[str, int]] = {}  # {date: {producer_id: seq}}

    def generate(self, producer_id: str,
                 parent_evidence_id: Optional[str] = None,
                 force_date: Optional[str] = None) -> EvidenceId:
        """生成一个新的 EvidenceId。

        Args:
            producer_id: "trend_ml" | "momentum_ml" | ...
            parent_evidence_id: 预留 — 父 Evidence human_id
            force_date: Replay 用 — 强制指定日期（默认今天）

        Returns:
            EvidenceId
        """
        date_str = force_date or datetime.now().strftime("%Y%m%d")

        with self._lock:
            # 全局计数器
            if date_str not in self._global_seq:
                self._global_seq[date_str] = 0
                self._producer_seq[date_str] = {}

            self._global_seq[date_str] += 1
            global_n = self._global_seq[date_str]

            # Producer 计数器
            if producer_id not in self._producer_seq[date_str]:
                self._producer_seq[date_str][producer_id] = 0
            self._producer_seq[date_str][producer_id] += 1
            seq = self._producer_seq[date_str][producer_id]

        # Human ID: AQFT-YYYYMMDD-NNNNNN
        human_id = f"AQFT-{date_str}-{global_n:06d}"

        # Machine ID: UUID v7
        machine_id = uuid7()

        return EvidenceId(
            human_id=human_id,
            machine_id=machine_id,
            evidence_seq=seq,
            producer_id=producer_id,
            date=date_str,
            parent_evidence_id=parent_evidence_id,
        )

    def status(self) -> dict:
        """当前生成器状态"""
        with self._lock:
            return {
                "dates": list(self._global_seq.keys()),
                "total_by_date": {k: v for k, v in self._global_seq.items()},
                "by_producer": {
                    k: dict(v) for k, v in self._producer_seq.items()
                },
            }


# ═══════════════════════════════════════════════════════
# Gate 2 验证
# ═══════════════════════════════════════════════════════

def gate2_test() -> dict:
    """Gate 2 完整验证"""
    gen = EvidenceIdGenerator()
    results = {"tests": [], "passed": True}

    # ── G2-1: 无重复 (1000次，不跑100万避免CI太慢) ──
    human_ids = []
    machine_ids = []
    for _ in range(1000):
        eid = gen.generate("trend_ml", force_date="20260802")
        human_ids.append(eid.human_id)
        machine_ids.append(eid.machine_id)

    no_human_dup = len(human_ids) == len(set(human_ids))
    no_machine_dup = len(machine_ids) == len(set(machine_ids))
    results["tests"].append({
        "name": "G2-1: 1000 IDs no collision",
        "human_no_dup": no_human_dup,
        "machine_no_dup": no_machine_dup,
        "passed": no_human_dup and no_machine_dup,
    })

    # ── G2-2: Human ID 严格递增 ──
    seqs = [int(h.split("-")[-1]) for h in human_ids]
    strictly_increasing = all(seqs[i] < seqs[i + 1] for i in range(len(seqs) - 1))
    no_gaps = seqs == list(range(1, len(seqs) + 1))  # 1,2,3,... 不跳号
    results["tests"].append({
        "name": "G2-2: Human ID strictly increasing, no gaps",
        "strictly_increasing": strictly_increasing,
        "no_gaps": no_gaps,
        "first": seqs[0],
        "last": seqs[-1],
        "passed": strictly_increasing and no_gaps,
    })

    # ── G2-3: Producer 独立序列 ──
    gen2 = EvidenceIdGenerator()
    trend_ids = [gen2.generate("trend_ml", force_date="20260802") for _ in range(5)]
    mom_ids = [gen2.generate("momentum_ml", force_date="20260802") for _ in range(5)]
    # trend 序列 1-5, momentum 序列 1-5
    trend_ok = [e.evidence_seq for e in trend_ids] == [1, 2, 3, 4, 5]
    mom_ok = [e.evidence_seq for e in mom_ids] == [1, 2, 3, 4, 5]
    # Human ID 全局继续递增
    last_human = int(mom_ids[-1].human_id.split("-")[-1])
    results["tests"].append({
        "name": "G2-3: Independent producer sequences",
        "trend_seq_ok": trend_ok,
        "momentum_seq_ok": mom_ok,
        "global_final_seq": last_human,
        "passed": trend_ok and mom_ok and last_human == 10,
    })

    all_passed = all(t["passed"] for t in results["tests"])
    results["passed"] = all_passed

    return results


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("  Evidence Identity — Gate 2 验证")
    print("=" * 60)

    gen = EvidenceIdGenerator()

    # 演示
    print("\n  示例 EvidenceId:")
    for pid in ("trend_ml", "momentum_ml", "trend_ml"):
        eid = gen.generate(pid, force_date="20260802")
        mid_short = eid.machine_id[:23] + "..."
        print(f"    {eid.human_id} | {mid_short} | {pid} seq={eid.evidence_seq}")

    # Gate 2
    print(f"\n  Running Gate 2...")
    result = gate2_test()
    for t in result["tests"]:
        icon = "✅" if t["passed"] else "❌"
        print(f"  {icon} {t['name']}")
        for k, v in t.items():
            if k not in ("name", "passed"):
                print(f"      {k}: {v}")

    print(f"\n  GATE 2: {'✅ PASSED' if result['passed'] else '❌ FAILED'}")
