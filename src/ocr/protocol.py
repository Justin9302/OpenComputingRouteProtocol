"""
OCR 协议消息格式定义与序列化

阶段一使用 UDP + JSON 进行节点间资源通告扩散。
消息设计对齐 RFC-005（设备级自治与资源表）和 BGP 路由协议思想。

消息类型:
- ResourceAdvertisement: 资源通告，携带资源表项向邻居扩散
- ResourceRequest: 请求邻居的完整资源表（新节点加入时用）
- ResourceResponse: 响应 ResourceRequest，发送完整资源表
"""

import json
import time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any


# 消息类型常量
MSG_RESOURCE_ADVERTISEMENT = "resource_advertisement"
MSG_RESOURCE_REQUEST = "resource_request"
MSG_RESOURCE_RESPONSE = "resource_response"


@dataclass
class ResourceEntry:
    """
    资源表项，对齐 spec/resource-table.schema.json
    类比路由表的路由条目。
    """
    resource_id: str                    # 资源唯一标识，如 server/ap-southeast-02
    resource_type: str                  # compute / storage / energy_grid / battery / other
    status: str                         # up / degraded / down
    price_per_mwh: float = 0.0          # 区域电价（本币/MWh，可为负）
    green_ratio: float = 0.0            # 绿电比例（0~1）
    avail_compute: Dict[str, Any] = field(default_factory=dict)  # 可用算力
    battery_soc: Optional[float] = None  # 储能剩余电量（0~1）
    latency_ms: float = 0.0             # 到本设备的网络时延
    network_zone: Optional[str] = None  # 网络区域（P2-7 预留）
    co2_intensity: Optional[float] = None  # 碳排放强度
    ttl: str = "300s"                   # 表项有效期
    trust_anchor: str = ""              # 身份来源（SPIFFE ID）
    # 内部字段（不序列化到 spec，但用于扩散）
    origin_node: str = ""               # 原始发布节点
    path_vector: List[str] = field(default_factory=list)  # 经过的节点列表（防环路）
    received_at: float = field(default_factory=time.time)  # 接收时间戳

    def is_expired(self) -> bool:
        """检查表项是否过期（基于 TTL）"""
        ttl_sec = self._parse_duration(self.ttl)
        return (time.time() - self.received_at) > ttl_sec

    @staticmethod
    def _parse_duration(s: str) -> int:
        """解析时长字符串，如 300s / 5m / 1h"""
        if not s:
            return 300
        s = s.strip().lower()
        if s.endswith("ms"):
            return int(s[:-2]) / 1000
        elif s.endswith("s"):
            return int(s[:-1])
        elif s.endswith("m"):
            return int(s[:-1]) * 60
        elif s.endswith("h"):
            return int(s[:-1]) * 3600
        return 300

    def to_dict(self) -> Dict:
        """序列化为字典（用于 JSON 传输）"""
        d = asdict(self)
        # 内部字段不对外传输时可保留，接收方用于防环路和老化
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "ResourceEntry":
        """从字典反序列化"""
        # 过滤掉未知字段，保持向后兼容
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class Message:
    """
    OCR 节点间通信消息
    """
    message_type: str
    sender_id: str
    entries: List[ResourceEntry] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    path_vector: List[str] = field(default_factory=list)  # 本条消息经过的节点
    seq: int = 0  # 序列号，防重放

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        d = asdict(self)
        d["entries"] = [e.to_dict() for e in self.entries]
        return json.dumps(d, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Message":
        """从 JSON 字符串反序列化"""
        d = json.loads(raw)
        entries = [ResourceEntry.from_dict(e) for e in d.get("entries", [])]
        return cls(
            message_type=d["message_type"],
            sender_id=d["sender_id"],
            entries=entries,
            timestamp=d.get("timestamp", time.time()),
            path_vector=d.get("path_vector", []),
            seq=d.get("seq", 0),
        )


def make_advertisement(sender_id: str, entries: List[ResourceEntry],
                       path_vector: Optional[List[str]] = None,
                       seq: int = 0) -> Message:
    """构造资源通告消息"""
    return Message(
        message_type=MSG_RESOURCE_ADVERTISEMENT,
        sender_id=sender_id,
        entries=entries,
        path_vector=path_vector or [sender_id],
        seq=seq,
    )


def make_request(sender_id: str) -> Message:
    """构造资源表请求消息（新节点加入时用）"""
    return Message(
        message_type=MSG_RESOURCE_REQUEST,
        sender_id=sender_id,
        path_vector=[sender_id],
    )


def make_response(sender_id: str, entries: List[ResourceEntry]) -> Message:
    """构造资源表响应消息"""
    return Message(
        message_type=MSG_RESOURCE_RESPONSE,
        sender_id=sender_id,
        entries=entries,
        path_vector=[sender_id],
    )
