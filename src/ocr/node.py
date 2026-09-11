"""
OCR P2P 节点实现

对齐 RFC-005 设备级自治：
- 每台设备独立维护资源表
- 节点间通过资源通告（Resource Advertisement）交换表项
- 路径向量（Path Vector）防环路
- 分裂水平（Split Horizon）避免从收到的接口发回
- 无中央控制器，拓扑变化自动收敛

使用 asyncio + UDP，零外部依赖。
HTTP 查询接口用内置 http.server，方便调试。
"""

import asyncio
import json
import time
import logging
import argparse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Optional, Set, Tuple

from .protocol import (
    Message, ResourceEntry,
    MSG_RESOURCE_ADVERTISEMENT, MSG_RESOURCE_REQUEST, MSG_RESOURCE_RESPONSE,
    make_advertisement, make_request, make_response,
)
from .resource_table import ResourceTable

logger = logging.getLogger("ocr.node")

# 扩散参数
ADVERTISE_INTERVAL = 30.0   # 定期通告间隔（秒）
PRUNE_INTERVAL = 10.0       # 老化检查间隔（秒）
MAX_PATH_LENGTH = 16        # 路径向量最大长度，防止环路


class OCRNodeProtocol(asyncio.DatagramProtocol):
    """UDP 协议处理器"""

    def __init__(self, node: "OCRNode"):
        self.node = node
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        logger.info(f"[{self.node.node_id}] UDP listening on {self.node.listen_addr}")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        try:
            msg = Message.from_json(data.decode("utf-8"))
            asyncio.ensure_future(self.node.handle_message(msg, addr))
        except Exception as e:
            logger.warning(f"[{self.node.node_id}] Failed to parse message from {addr}: {e}")

    def error_received(self, exc):
        logger.warning(f"[{self.node.node_id}] UDP error: {exc}")


class OCRNode:
    """
    OCR 设备级自治节点

    每个节点:
    - 维护本地资源表
    - 与邻居交换资源通告
    - 被动接收外部数据（如电价推送）
    - 提供 HTTP 接口查询资源表
    """

    def __init__(self, node_id: str, listen_host: str = "0.0.0.0",
                 listen_port: int = 9000, http_port: int = 8080,
                 peers: Optional[List[str]] = None):
        self.node_id = node_id
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.http_port = http_port
        self.listen_addr = f"{listen_host}:{listen_port}"

        # 邻居列表: "host:port" 格式
        self.peers: Set[str] = set(peers or [])

        # 记录从哪个邻居收到的表项（用于分裂水平）
        # resource_id -> peer_addr
        self._entry_source: Dict[str, str] = {}

        # 本地资源表
        self.resource_table = ResourceTable(device_id=node_id)

        # 运行状态
        self._running = False
        self._transport = None
        self._protocol = None
        self._http_server = None
        self._http_thread = None

    def add_peer(self, peer_addr: str):
        """添加邻居"""
        self.peers.add(peer_addr)
        logger.info(f"[{self.node_id}] Peer added: {peer_addr}")

    def remove_peer(self, peer_addr: str):
        """移除邻居"""
        self.peers.discard(peer_addr)
        logger.info(f"[{self.node_id}] Peer removed: {peer_addr}")

    def update_local_compute(self, entry: ResourceEntry):
        """更新本地算力状态（本节点产生的表项）"""
        self.resource_table.upsert_local(entry)
        # 触发更新：立即向邻居通告
        asyncio.ensure_future(self._advertise_to_peers(triggered=True))

    def ingest_external_data(self, entry: ResourceEntry, source: str = "external"):
        """
        被动接收外部数据（如电价推送、储能状态）。
        对齐 RFC-005 §3.2 设备级被动接收。
        """
        entry.origin_node = self.node_id
        entry.received_at = time.time()
        if self.resource_table.upsert(entry):
            logger.info(f"[{self.node_id}] External data ingested: {entry.resource_type}:{entry.resource_id} "
                        f"price={entry.price_per_mwh:+.0f} green={entry.green_ratio:.2f}")
            # 触发更新
            asyncio.ensure_future(self._advertise_to_peers(triggered=True))

    async def handle_message(self, msg: Message, addr: Tuple[str, int]):
        """处理收到的消息"""
        peer_addr = f"{addr[0]}:{addr[1]}"

        # 路径向量防环路：如果本节点已在路径中，丢弃
        if self.node_id in msg.path_vector:
            logger.debug(f"[{self.node_id}] Loop detected, dropping message from {msg.sender_id}")
            return

        if msg.message_type == MSG_RESOURCE_ADVERTISEMENT:
            await self._handle_advertisement(msg, peer_addr)
        elif msg.message_type == MSG_RESOURCE_REQUEST:
            await self._handle_request(msg, peer_addr)
        elif msg.message_type == MSG_RESOURCE_RESPONSE:
            await self._handle_response(msg, peer_addr)

    async def _handle_advertisement(self, msg: Message, peer_addr: str):
        """处理资源通告"""
        updated = False
        for entry in msg.entries:
            # 更新路径向量
            entry.path_vector = msg.path_vector + [self.node_id]
            entry.received_at = time.time()

            # 防回滚检查在 upsert 内部完成
            if self.resource_table.upsert(entry):
                self._entry_source[entry.resource_id] = peer_addr
                updated = True

        if updated:
            logger.debug(f"[{self.node_id}] Table updated from {msg.sender_id} via {peer_addr}. "
                         f"{self.resource_table.summary()}")
            # 触发更新：向其他邻居扩散
            await self._advertise_to_peers(triggered=True, exclude_peer=peer_addr)

    async def _handle_request(self, msg: Message, peer_addr: str):
        """处理资源表请求（新节点加入时）"""
        logger.info(f"[{self.node_id}] Resource request from {msg.sender_id}")
        entries = self.resource_table.get_advertisements()
        response = make_response(self.node_id, entries)
        self._send_message(response, peer_addr)

    async def _handle_response(self, msg: Message, peer_addr: str):
        """处理资源表响应"""
        for entry in msg.entries:
            entry.path_vector = msg.path_vector + [self.node_id]
            entry.received_at = time.time()
            if self.resource_table.upsert(entry):
                self._entry_source[entry.resource_id] = peer_addr
        logger.info(f"[{self.node_id}] Synced {len(msg.entries)} entries from {msg.sender_id}")

    async def _advertise_to_peers(self, triggered: bool = False,
                                  exclude_peer: Optional[str] = None):
        """向邻居通告资源表"""
        entries = self.resource_table.get_advertisements()
        if not entries:
            return

        # 分批通告（避免 UDP 包过大），每批最多 50 条
        batch_size = 50
        for i in range(0, len(entries), batch_size):
            batch = entries[i:i + batch_size]
            msg = make_advertisement(
                sender_id=self.node_id,
                entries=batch,
                path_vector=[self.node_id],
                seq=self.resource_table.next_seq(),
            )
            for peer in self.peers:
                # 分裂水平：不从收到该表项的邻居发回
                # 简化实现：如果该批次所有表项都来自该邻居，则跳过
                if exclude_peer and peer == exclude_peer:
                    continue
                all_from_peer = all(
                    self._entry_source.get(e.resource_id) == peer
                    for e in batch
                )
                if all_from_peer:
                    continue
                self._send_message(msg, peer)

        if triggered:
            logger.debug(f"[{self.node_id}] Triggered advertisement: {len(entries)} entries to {len(self.peers)} peers")

    def _send_message(self, msg: Message, peer_addr: str):
        """发送消息到指定邻居"""
        if self._transport is None:
            return
        try:
            host, port = peer_addr.rsplit(":", 1)
            data = msg.to_json().encode("utf-8")
            self._transport.sendto(data, (host, int(port)))
        except Exception as e:
            logger.warning(f"[{self.node_id}] Failed to send to {peer_addr}: {e}")

    async def _periodic_advertise(self):
        """定期通告任务"""
        while self._running:
            await asyncio.sleep(ADVERTISE_INTERVAL)
            await self._advertise_to_peers()

    async def _periodic_prune(self):
        """定期老化任务"""
        while self._running:
            await asyncio.sleep(PRUNE_INTERVAL)
            expired = self.resource_table.prune_expired()
            if expired:
                logger.info(f"[{self.node_id}] Pruned {len(expired)} expired entries: {expired}")

    def _start_http_server(self):
        """启动 HTTP 查询接口（用于调试和验证）"""
        node = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/resource-table":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(node.resource_table.to_json().encode("utf-8"))
                elif self.path == "/peers":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "node_id": node.node_id,
                        "peers": sorted(node.peers),
                    }).encode("utf-8"))
                elif self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "status": "ok",
                        "node_id": node.node_id,
                        "entries": len(node.resource_table.entries),
                    }).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass  # 静默 HTTP 日志

        class ReusableHTTPServer(HTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self._http_server = ReusableHTTPServer((self.listen_host, self.http_port), Handler)
        self._http_thread = threading.Thread(target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()
        logger.info(f"[{self.node_id}] HTTP API on http://{self.listen_host}:{self.http_port}")

    async def start(self):
        """启动节点"""
        self._running = True

        # 启动 UDP
        loop = asyncio.get_event_loop()
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: OCRNodeProtocol(self),
            local_addr=(self.listen_host, self.listen_port),
        )

        # 启动 HTTP
        self._start_http_server()

        # 启动后台任务
        asyncio.ensure_future(self._periodic_advertise())
        asyncio.ensure_future(self._periodic_prune())

        # 向邻居请求完整资源表（新节点加入同步）
        if self.peers:
            await asyncio.sleep(1)  # 等待 UDP 就绪
            request = make_request(self.node_id)
            for peer in self.peers:
                self._send_message(request, peer)
            logger.info(f"[{self.node_id}] Requested resource table from {len(self.peers)} peers")

        logger.info(f"[{self.node_id}] Node started. Peers: {sorted(self.peers)}")

    async def stop(self):
        """停止节点"""
        self._running = False
        if self._transport:
            self._transport.close()
            self._transport = None
        if self._http_server:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
        if self._http_thread:
            self._http_thread.join(timeout=2)
            self._http_thread = None
        logger.info(f"[{self.node_id}] Node stopped")


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description="OCR 设备级自治节点")
    parser.add_argument("--node-id", required=True, help="节点唯一标识")
    parser.add_argument("--listen-host", default="0.0.0.0", help="UDP 监听地址")
    parser.add_argument("--listen-port", type=int, default=9000, help="UDP 监听端口")
    parser.add_argument("--http-port", type=int, default=8080, help="HTTP 查询端口")
    parser.add_argument("--peers", default="", help="邻居列表，逗号分隔，如 192.168.1.2:9000,192.168.1.3:9000")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    peers = [p.strip() for p in args.peers.split(",") if p.strip()] if args.peers else []

    node = OCRNode(
        node_id=args.node_id,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        http_port=args.http_port,
        peers=peers,
    )

    loop = asyncio.get_event_loop()
    loop.run_until_complete(node.start())

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.run_until_complete(node.stop())


if __name__ == "__main__":
    main()
