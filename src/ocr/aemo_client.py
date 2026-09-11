"""
AEMO 电价数据客户端

从澳洲电力市场（AEMO）获取真实电价数据，支持：
- 从 AEMO 公开 CSV 数据源下载
- 本地 CSV 文件读取
- 数据缓存（避免重复请求）
- 按区域和时间范围查询
- 模拟数据 fallback（网络不可用时）

AEMO 数据格式参考：
- SETTLEMENTDATE: 结算时间（5分钟粒度）
- REGION: 区域（NSW1/QLD1/SA1/TAS1/VIC1）
- RRP: 区域参考价格（AUD/MWh）
- TOTALDEMAND: 总需求（MW）
"""

import csv
import json
import os
import time
import logging
import urllib.request
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("ocr.aemo")

# AEMO 公开数据 URL（聚合数据，CSV 格式）
# 注意：AEMO 网站结构可能变化，这里提供常见入口
AEMO_AGGREGATED_DATA_URL = "https://aemo.com.au/-/media/files/energy/electricity/nem/aggregated-data"

# 支持的 NEM 区域
NEM_REGIONS = ["NSW1", "QLD1", "SA1", "TAS1", "VIC1"]

# 缓存目录
DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cache")


class AEMOClient:
    """AEMO 电价数据客户端"""

    def __init__(self, cache_dir: Optional[str] = None, region: str = "NSW1"):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.region = region
        os.makedirs(self.cache_dir, exist_ok=True)

    def fetch_recent(self, days: int = 7, region: Optional[str] = None) -> List[Dict]:
        """
        获取最近 N 天的电价数据。
        优先从 AEMO 下载，失败则用模拟数据。
        """
        region = region or self.region
        cache_file = self._cache_path(region, days)

        # 检查缓存（24小时内有效）
        if os.path.exists(cache_file):
            age = time.time() - os.path.getmtime(cache_file)
            if age < 86400:  # 24小时
                logger.info(f"Using cached AEMO data: {cache_file}")
                return self._load_cache(cache_file)

        # 尝试从 AEMO 下载
        try:
            data = self._download_from_aemo(region, days)
            if data:
                self._save_cache(cache_file, data)
                logger.info(f"Fetched {len(data)} records from AEMO for {region}")
                return data
        except Exception as e:
            logger.warning(f"Failed to fetch from AEMO: {e}, using mock data")

        # Fallback: 模拟数据
        logger.info("Using mock price data (AEMO fetch failed)")
        return self._generate_mock(region, days)

    def get_current_price(self, region: Optional[str] = None) -> Optional[float]:
        """获取当前最新电价（AUD/MWh）"""
        data = self.fetch_recent(days=1, region=region)
        if data:
            return data[-1]["price_per_mwh"]
        return None

    def get_price_at(self, dt: datetime, region: Optional[str] = None) -> Optional[float]:
        """获取指定时间的电价（最近的5分钟结算点）"""
        region = region or self.region
        data = self.fetch_recent(days=7, region=region)
        # 找到最接近的时间点
        target = dt.replace(second=0, microsecond=0)
        best = None
        best_diff = float("inf")
        for record in data:
            record_time = datetime.fromisoformat(record["time"])
            diff = abs((record_time - target).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best = record
        return best["price_per_mwh"] if best else None

    def get_negative_price_periods(self, days: int = 7,
                                    region: Optional[str] = None) -> List[Dict]:
        """获取负电价时段列表"""
        data = self.fetch_recent(days=days, region=region)
        return [r for r in data if r["price_per_mwh"] < 0]

    def get_price_stats(self, days: int = 7,
                        region: Optional[str] = None) -> Dict:
        """获取电价统计信息"""
        data = self.fetch_recent(days=days, region=region)
        if not data:
            return {}
        prices = [r["price_per_mwh"] for r in data]
        return {
            "region": region or self.region,
            "period_days": days,
            "records": len(data),
            "min": min(prices),
            "max": max(prices),
            "avg": sum(prices) / len(prices),
            "negative_count": sum(1 for p in prices if p < 0),
            "negative_ratio": sum(1 for p in prices if p < 0) / len(prices),
        }

    def _download_from_aemo(self, region: str, days: int) -> List[Dict]:
        """
        从 AEMO 下载电价数据。
        AEMO 公开数据通常需要从他们的网站获取 CSV。
        这里实现一个通用的 CSV 下载和解析。
        """
        # AEMO 聚合数据 URL 格式（可能需要调整）
        # 实际使用时可能需要从 AEMO 网站获取最新的下载链接
        url = f"{AEMO_AGGREGATED_DATA_URL}/prices-and-demand.csv"

        logger.info(f"Downloading AEMO data from {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "OCR-Bot/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode("utf-8")

        return self._parse_aemo_csv(content, region)

    def _parse_aemo_csv(self, content: str, region: str) -> List[Dict]:
        """解析 AEMO 格式的 CSV 数据"""
        records = []
        reader = csv.DictReader(content.splitlines())

        for row in reader:
            # AEMO CSV 字段可能有不同名称，尝试多种
            record_region = row.get("REGION") or row.get("region") or ""
            if region and record_region != region:
                continue

            time_str = row.get("SETTLEMENTDATE") or row.get("settlementdate") or ""
            price_str = row.get("RRP") or row.get("rrp") or row.get("price") or "0"
            demand_str = row.get("TOTALDEMAND") or row.get("totaldemand") or "0"

            try:
                # AEMO 时间格式: "2024/01/01 00:05" 或 "2024-01-01 00:05:00"
                time_str = time_str.replace("/", "-")
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
                price = float(price_str)
                demand = float(demand_str) if demand_str else 0.0

                records.append({
                    "time": dt.isoformat(),
                    "region": record_region or region,
                    "price_per_mwh": price,
                    "demand_mw": demand,
                })
            except (ValueError, KeyError) as e:
                logger.debug(f"Skip row: {e}")
                continue

        # 按时间排序
        records.sort(key=lambda x: x["time"])
        return records

    def load_from_csv(self, filepath: str, region: Optional[str] = None) -> List[Dict]:
        """从本地 CSV 文件加载电价数据"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"CSV file not found: {filepath}")
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        return self._parse_aemo_csv(content, region or self.region)

    def _generate_mock(self, region: str, days: int) -> List[Dict]:
        """生成模拟电价数据（基于 NEM 典型曲线）"""
        records = []
        base_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        base_time -= timedelta(days=days)

        # 典型日曲线（30分钟粒度，48个点）
        # 深夜负电价，早晚高峰高电价
        daily_curve = [
            -30, -25, -20, -15, -10, -5, 0, 5,          # 00:00-04:00
            20, 45, 80, 120, 160, 210, 280, 300,        # 04:00-08:00
            260, 220, 180, 140, 110, 90, 75, 60,        # 08:00-12:00
            50, 40, 30, 25, 20, 15, 10, 8,              # 12:00-16:00
            30, 80, 150, 240, 320, 360, 340, 280,       # 16:00-20:00
            200, 140, 90, 50, 20, -5, -15, -25,         # 20:00-24:00
        ]

        import random
        rng = random.Random(hash(region) % 10000)

        for day in range(days):
            for i, base_price in enumerate(daily_curve):
                dt = base_time + timedelta(days=day, minutes=30 * i)
                # 添加随机波动
                jitter = rng.uniform(-20, 20)
                # 周末电价略低
                if dt.weekday() >= 5:
                    jitter -= 30
                price = round(base_price + jitter, 1)
                records.append({
                    "time": dt.isoformat(),
                    "region": region,
                    "price_per_mwh": price,
                    "demand_mw": round(8000 + rng.uniform(-1000, 1000), 1),
                })

        return records

    def _cache_path(self, region: str, days: int) -> str:
        """生成缓存文件路径"""
        date_str = datetime.now().strftime("%Y%m%d")
        return os.path.join(self.cache_dir, f"aemo_{region}_{days}d_{date_str}.json")

    def _save_cache(self, path: str, data: List[Dict]):
        """保存数据到缓存"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_cache(self, path: str) -> List[Dict]:
        """从缓存加载数据"""
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def main():
    """命令行测试"""
    import argparse
    parser = argparse.ArgumentParser(description="AEMO 电价数据客户端测试")
    parser.add_argument("--region", default="NSW1", help="NEM 区域")
    parser.add_argument("--days", type=int, default=7, help="获取最近 N 天数据")
    parser.add_argument("--stats", action="store_true", help="显示统计信息")
    parser.add_argument("--negative", action="store_true", help="显示负电价时段")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    client = AEMOClient(region=args.region)
    data = client.fetch_recent(days=args.days)

    print(f"\n获取到 {len(data)} 条电价记录（{args.region}）")
    print(f"时间范围: {data[0]['time']} ~ {data[-1]['time']}")

    if args.stats:
        stats = client.get_price_stats(days=args.days)
        print(f"\n统计信息:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

    if args.negative:
        negative = client.get_negative_price_periods(days=args.days)
        print(f"\n负电价时段（共 {len(negative)} 个）:")
        for r in negative[:10]:
            print(f"  {r['time']}: {r['price_per_mwh']:+.1f} AUD/MWh")
        if len(negative) > 10:
            print(f"  ... 还有 {len(negative) - 10} 个")


if __name__ == "__main__":
    main()
