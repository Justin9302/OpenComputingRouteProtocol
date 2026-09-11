#!/bin/bash
# OCR 设备级自治 Demo 一键启动脚本
# 启动 3 个节点 + 1 个电价数据源，验证资源表扩散与收敛
#
# 用法:
#   ./scripts/run-demo-autonomy.sh          # 本地启动（localhost）
#   ./scripts/run-demo-autonomy.sh --multi  # 多虚拟机模式（需手动配置节点地址）
#
# 节点布局:
#   node1: UDP 9001, HTTP 8081
#   node2: UDP 9002, HTTP 8082
#   node3: UDP 9003, HTTP 8083
#   energy_source: 推送到 node1

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# 确保 Python 能找到 src 模块
export PYTHONPATH="$PROJECT_DIR/src:$PYTHONPATH"

# 日志目录
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

echo "=============================================="
echo "  OCR 设备级自治 Demo"
echo "  3 节点 + 电价数据源"
echo "=============================================="
echo ""

# 清理旧进程
pkill -f "ocr.node" 2>/dev/null || true
pkill -f "ocr.energy_source" 2>/dev/null || true
sleep 1

echo "[1/4] 启动 node1 (UDP:9001, HTTP:8081)..."
python -m ocr.node \
  --node-id hub/edge-01 \
  --listen-port 9001 \
  --http-port 8081 \
  --peers "127.0.0.1:9002,127.0.0.1:9003" \
  > "$LOG_DIR/node1.log" 2>&1 &
echo "  PID: $!"

echo "[2/4] 启动 node2 (UDP:9002, HTTP:8082)..."
python -m ocr.node \
  --node-id hub/edge-02 \
  --listen-port 9002 \
  --http-port 8082 \
  --peers "127.0.0.1:9001" \
  > "$LOG_DIR/node2.log" 2>&1 &
echo "  PID: $!"

echo "[3/4] 启动 node3 (UDP:9003, HTTP:8083)..."
python -m ocr.node \
  --node-id hub/edge-03 \
  --listen-port 9003 \
  --http-port 8083 \
  --peers "127.0.0.1:9001" \
  > "$LOG_DIR/node3.log" 2>&1 &
echo "  PID: $!"

sleep 2

echo "[4/4] 启动电价数据源（推送到 node1）..."
python -m ocr.energy_source \
  --source-id aemo-nsw1 \
  --region NSW1 \
  --targets "127.0.0.1:9001" \
  --interval 3 \
  --loop \
  > "$LOG_DIR/energy_source.log" 2>&1 &
echo "  PID: $!"

echo ""
echo "=============================================="
echo "  启动完成！等待 5 秒让数据扩散..."
echo "=============================================="
sleep 5

echo ""
echo "--- node1 资源表 ---"
curl -s http://127.0.0.1:8081/resource-table | python -m json.tool 2>/dev/null || echo "  (查询失败)"

echo ""
echo "--- node2 资源表（验证扩散）---"
curl -s http://127.0.0.1:8082/resource-table | python -m json.tool 2>/dev/null || echo "  (查询失败)"

echo ""
echo "--- node3 资源表（验证扩散）---"
curl -s http://127.0.0.1:8083/resource-table | python -m json.tool 2>/dev/null || echo "  (查询失败)"

echo ""
echo "=============================================="
echo "  Demo 运行中"
echo "  HTTP 查询接口:"
echo "    node1: http://127.0.0.1:8081/resource-table"
echo "    node2: http://127.0.0.1:8082/resource-table"
echo "    node3: http://127.0.0.1:8083/resource-table"
echo ""
echo "  日志: $LOG_DIR/"
echo "  停止: pkill -f 'ocr.node' && pkill -f 'ocr.energy_source'"
echo "=============================================="

# 保持脚本运行，方便查看
wait
