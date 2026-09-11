#!/usr/bin/env python3
"""
OCR 设备级自治自动验证脚本

验证场景:
1. 启动 3 个节点，互相连接
2. 向 node1 推送电价数据
3. 验证数据扩散到 node2 和 node3
4. 杀掉 node2，验证 node1/node3 仍正常工作
5. 重启 node2，验证重新收敛

用法:
  python scripts/verify-autonomy.py
"""

import asyncio
import json
import sys
import os
import time
import urllib.request

# 确保能导入 src 模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ocr.node import OCRNode
from ocr.energy_source import EnergySource
from ocr.protocol import ResourceEntry


def query_resource_table(port: int) -> dict:
    """通过 HTTP 查询节点资源表"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/resource-table", timeout=3) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def check_entry(data: dict, resource_id: str) -> bool:
    """检查资源表中是否包含指定表项"""
    if "entries" not in data:
        return False
    return any(e.get("resource_id") == resource_id for e in data["entries"])


async def main():
    print("=" * 60)
    print("  OCR 设备级自治验证")
    print("=" * 60)

    # 启动 3 个节点
    print("\n[1] 启动 3 个节点...")
    node1 = OCRNode(node_id="node1", listen_port=19001, http_port=18081,
                    peers=["127.0.0.1:19002", "127.0.0.1:19003"])
    node2 = OCRNode(node_id="node2", listen_port=19002, http_port=18082,
                    peers=["127.0.0.1:19001"])
    node3 = OCRNode(node_id="node3", listen_port=19003, http_port=18083,
                    peers=["127.0.0.1:19001"])

    await node1.start()
    await node2.start()
    await node3.start()
    await asyncio.sleep(1)
    print("  ✅ 3 个节点已启动")

    # 向 node1 推送电价数据
    print("\n[2] 向 node1 推送电价数据（模拟 AEMO）...")
    source = EnergySource(source_id="aemo-test", region="NSW1")
    data = source.generate_mock(seed=42)
    await source.push_to("127.0.0.1:19001", data[0]["price"], data[0]["green"])
    await asyncio.sleep(2)  # 等待扩散

    # 验证扩散
    print("\n[3] 验证数据扩散...")
    rt1 = query_resource_table(18081)
    rt2 = query_resource_table(18082)
    rt3 = query_resource_table(18083)

    has1 = check_entry(rt1, "energy_grid/NSW1")
    has2 = check_entry(rt2, "energy_grid/NSW1")
    has3 = check_entry(rt3, "energy_grid/NSW1")

    print(f"  node1 有电价表项: {'✅' if has1 else '❌'} ({rt1.get('entry_count', 0)} entries)")
    print(f"  node2 有电价表项: {'✅' if has2 else '❌'} ({rt2.get('entry_count', 0)} entries)")
    print(f"  node3 有电价表项: {'✅' if has3 else '❌'} ({rt3.get('entry_count', 0)} entries)")

    if has2 and has3:
        print("  ✅ 数据扩散验证通过！电价数据从 node1 扩散到了 node2 和 node3")
    else:
        print("  ❌ 数据扩散验证失败")

    # 验证节点本地算力状态更新
    print("\n[4] 验证本地算力状态更新与扩散...")
    compute_entry = ResourceEntry(
        resource_id="server/node1-gpu",
        resource_type="compute",
        status="up",
        price_per_mwh=data[0]["price"],
        green_ratio=data[0]["green"],
        avail_compute={"gpu_count": 8, "vram_gb": 640, "gpu_model": "H100-80GB"},
        latency_ms=20,
        ttl="300s",
        trust_anchor="spiffe://ocr/node1",
    )
    node1.update_local_compute(compute_entry)
    await asyncio.sleep(2)

    rt2_after = query_resource_table(18082)
    has_compute = check_entry(rt2_after, "server/node1-gpu")
    print(f"  node2 有 node1 的算力表项: {'✅' if has_compute else '❌'}")

    # 杀掉 node2，验证容错
    print("\n[5] 验证节点离线容错（停止 node2）...")
    await node2.stop()
    await asyncio.sleep(1)

    rt1_after = query_resource_table(18081)
    rt3_after = query_resource_table(18083)
    print(f"  node1 仍正常: {'✅' if 'error' not in rt1_after else '❌'} ({rt1_after.get('entry_count', 0)} entries)")
    print(f"  node3 仍正常: {'✅' if 'error' not in rt3_after else '❌'} ({rt3_after.get('entry_count', 0)} entries)")

    # 重启 node2，验证重新收敛（用不同端口避免 TIME_WAIT）
    print("\n[6] 验证节点重新加入与收敛...")
    node2_new = OCRNode(node_id="node2", listen_port=19004, http_port=18084,
                        peers=["127.0.0.1:19001"])
    await node2_new.start()
    await asyncio.sleep(3)  # 等待同步

    rt2_new = query_resource_table(18084)
    has_grid = check_entry(rt2_new, "energy_grid/NSW1")
    has_gpu = check_entry(rt2_new, "server/node1-gpu")
    print(f"  node2 重新同步到电价表项: {'✅' if has_grid else '❌'}")
    print(f"  node2 重新同步到算力表项: {'✅' if has_gpu else '❌'}")

    # 清理
    print("\n[7] 清理...")
    source.stop()
    await node1.stop()
    await node2_new.stop()
    await node3.stop()

    print("\n" + "=" * 60)
    print("  验证完成")
    print("=" * 60)

    # 汇总结果
    all_pass = has1 and has2 and has3 and has_compute and has_grid and has_gpu
    if all_pass:
        print("\n🎉 所有验证通过！设备级自治原型工作正常。")
        return 0
    else:
        print("\n⚠️  部分验证未通过，请检查日志。")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
