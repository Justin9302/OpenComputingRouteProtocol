"""
设备级资源表（Resource Table）核心实现

对齐 RFC-005：每台设备独立维护一张资源表，类比路由表。
资源表回答"计算任务何时、在哪儿、用什么电跑"。

核心功能:
- upsert: 被动接收数据后更新表项
- prune_expired: 清理过期表项（路由老化）
- query: 按类型/条件查询
- get_advertisements: 获取需要向邻居通告的表项
"""

import time
import json
from typing import Dict, List, Optional
from .protocol import ResourceEntry


class ResourceTable:
    """
    设备本地资源表：独立维护，无任何第三方参与。
    类比路由表，每条表项有 TTL，过期自动老化。
    """

    def __init__(self, device_id: str):
        self.device_id = device_id
        self.entries: Dict[str, ResourceEntry] = {}
        self.version = 1
        self._seq_counter = 0

    def upsert(self, entry: ResourceEntry) -> bool:
        """
        被动接收数据后更新表项。
        返回 True 如果是新增或有更新，False 如果无变化。

        防回滚：如果已有表项的 received_at 更新，则不更新。
        """
        existing = self.entries.get(entry.resource_id)
        if existing:
            # 防回滚：已有表项更新时间更新则忽略
            if entry.received_at < existing.received_at:
                return False
            # 如果内容完全相同，不更新版本
            if self._entries_equal(existing, entry):
                return False

        self.entries[entry.resource_id] = entry
        self.version += 1
        return True

    def upsert_local(self, entry: ResourceEntry) -> None:
        """
        更新本地产生的表项（如本节点的算力状态）。
        本地表项不老化（由节点自己维护状态），设置 origin_node 为本节点。
        """
        entry.origin_node = self.device_id
        entry.received_at = time.time()
        self.entries[entry.resource_id] = entry
        self.version += 1

    def remove(self, resource_id: str) -> bool:
        """手动删除表项"""
        if resource_id in self.entries:
            del self.entries[resource_id]
            self.version += 1
            return True
        return False

    def prune_expired(self) -> List[str]:
        """
        清理过期表项（路由老化）。
        本地产生的表项（origin_node == device_id）不自动老化。
        返回被删除的 resource_id 列表。
        """
        expired = []
        for rid, entry in list(self.entries.items()):
            if entry.origin_node == self.device_id:
                continue  # 本地表项不老化
            if entry.is_expired():
                expired.append(rid)
                del self.entries[rid]
        if expired:
            self.version += 1
        return expired

    def query(self, resource_type: Optional[str] = None,
              status: Optional[str] = None) -> List[ResourceEntry]:
        """查询表项，可按类型和状态过滤"""
        results = []
        for entry in self.entries.values():
            if resource_type and entry.resource_type != resource_type:
                continue
            if status and entry.status != status:
                continue
            results.append(entry)
        return results

    def get(self, resource_id: str) -> Optional[ResourceEntry]:
        """按 ID 获取单个表项"""
        return self.entries.get(resource_id)

    def get_all(self) -> List[ResourceEntry]:
        """获取所有表项"""
        return list(self.entries.values())

    def get_advertisements(self) -> List[ResourceEntry]:
        """
        获取需要向邻居通告的表项。
        排除从该邻居收到的表项（分裂水平 Split Horizon 的简化实现）。
        这里返回所有非过期表项，具体过滤在 node 层处理。
        """
        self.prune_expired()
        return list(self.entries.values())

    def next_seq(self) -> int:
        """生成下一个序列号"""
        self._seq_counter += 1
        return self._seq_counter

    def to_dict(self) -> Dict:
        """序列化为字典（用于 HTTP 查询或调试）"""
        entries = []
        for e in self.entries.values():
            d = e.to_dict()
            d["received_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(e.received_at)
            )
            entries.append(d)
        return {
            "device_id": self.device_id,
            "version": self.version,
            "entry_count": len(entries),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "entries": entries,
        }

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def summary(self) -> str:
        """返回简要摘要，用于日志"""
        by_type = {}
        for e in self.entries.values():
            by_type[e.resource_type] = by_type.get(e.resource_type, 0) + 1
        parts = [f"{t}:{c}" for t, c in sorted(by_type.items())]
        return f"[RT:{self.device_id}] v{self.version} ({', '.join(parts)})"

    @staticmethod
    def _entries_equal(a: ResourceEntry, b: ResourceEntry) -> bool:
        """比较两个表项内容是否相同（忽略 received_at 和 path_vector）"""
        return (
            a.resource_id == b.resource_id
            and a.resource_type == b.resource_type
            and a.status == b.status
            and a.price_per_mwh == b.price_per_mwh
            and a.green_ratio == b.green_ratio
            and a.avail_compute == b.avail_compute
            and a.battery_soc == b.battery_soc
            and a.ttl == b.ttl
            and a.trust_anchor == b.trust_anchor
        )
