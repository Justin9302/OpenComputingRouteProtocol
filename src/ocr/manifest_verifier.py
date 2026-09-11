"""
Manifest 清单验证器

对齐 RFC-003 §5.5-5.6，实现客户端敏感度清单的第一层验证：
- 插件身份与完整性（签名、版本、时间戳）
- 文件哈希抽样验证
- 信任分计算

第二层（环境健康声明）和第三层（TPM 远程证明）为企业版/远期功能，
本模块预留接口，暂不实现。
"""

import json
import hashlib
import os
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("ocr.manifest")

# 支持的插件版本列表（过旧版本不信任）
SUPPORTED_PLUGIN_VERSIONS = {
    "dsh-ocr-plugin": ["0.1.0", "0.2.0"],
}

# manifest 有效期（秒），默认 72 小时
DEFAULT_MANIFEST_TTL = 72 * 3600

# 默认抽样率
DEFAULT_SAMPLE_RATE = 0.1  # 10%
HIGH_SENSITIVITY_SAMPLE_RATE = 0.5  # high/critical 级别 50%

# 信任分级阈值
TRUST_HIGH_THRESHOLD = 80
TRUST_MEDIUM_THRESHOLD = 50


class ManifestVerificationResult:
    """验证结果"""

    def __init__(self):
        self.trusted = False
        self.trust_score = 0
        self.trust_level = "low"  # high / medium / low
        self.overall_sensitivity = "low"
        self.checks = {
            "format_valid": False,
            "signature_valid": False,
            "version_supported": False,
            "timestamp_fresh": False,
            "hash_sample_passed": False,
        }
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.files_verified = 0
        self.files_total = 0

    def to_dict(self) -> Dict:
        return {
            "trusted": self.trusted,
            "trust_score": self.trust_score,
            "trust_level": self.trust_level,
            "overall_sensitivity": self.overall_sensitivity,
            "checks": self.checks,
            "errors": self.errors,
            "warnings": self.warnings,
            "files_verified": self.files_verified,
            "files_total": self.files_total,
        }


class ManifestVerifier:
    """
    Manifest 验证器

    验证流程:
    1. 读取并解析 manifest JSON
    2. 验证格式完整性
    3. 验证插件签名（预留正式验签接口）
    4. 验证插件版本是否受支持
    5. 验证时间戳是否在有效期内
    6. 抽样验证文件 SHA256
    7. 计算信任分和信任级别
    """

    def __init__(self, data_dir: str,
                 supported_versions: Optional[Dict[str, List[str]]] = None,
                 manifest_ttl: int = DEFAULT_MANIFEST_TTL,
                 sample_rate: float = DEFAULT_SAMPLE_RATE):
        self.data_dir = data_dir
        self.supported_versions = supported_versions or SUPPORTED_PLUGIN_VERSIONS
        self.manifest_ttl = manifest_ttl
        self.sample_rate = sample_rate

    def verify(self, manifest_path: Optional[str] = None) -> ManifestVerificationResult:
        """
        验证 manifest 清单

        Args:
            manifest_path: manifest 文件路径，默认在 data_dir 下找 .ocr_manifest.json

        Returns:
            ManifestVerificationResult
        """
        result = ManifestVerificationResult()

        # 1. 查找并读取 manifest
        if manifest_path is None:
            manifest_path = os.path.join(self.data_dir, ".ocr_manifest.json")

        if not os.path.exists(manifest_path):
            result.errors.append(f"Manifest not found: {manifest_path}")
            return result

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except json.JSONDecodeError as e:
            result.errors.append(f"Invalid JSON: {e}")
            return result

        # 2. 验证格式
        if not self._validate_format(manifest, result):
            return result

        result.overall_sensitivity = manifest.get("overall_sensitivity", "low")

        # 3. 验证签名
        self._verify_signature(manifest, result)

        # 4. 验证版本
        self._verify_version(manifest, result)

        # 5. 验证时间戳
        self._verify_timestamp(manifest, result)

        # 6. 抽样验证哈希
        self._verify_file_hashes(manifest, result)

        # 7. 计算信任分
        self._calculate_trust_score(result)

        return result

    def _validate_format(self, manifest: Dict,
                         result: ManifestVerificationResult) -> bool:
        """验证 manifest 格式完整性"""
        required_fields = ["manifest_version", "scanned_by", "scanned_at",
                           "overall_sensitivity", "files"]
        for field in required_fields:
            if field not in manifest:
                result.errors.append(f"Missing required field: {field}")
                return False

        if not isinstance(manifest["files"], list):
            result.errors.append("'files' must be a list")
            return False

        if manifest["manifest_version"] != "1.0":
            result.warnings.append(
                f"Unsupported manifest version: {manifest['manifest_version']}")

        result.checks["format_valid"] = True
        result.files_total = len(manifest["files"])
        return True

    def _verify_signature(self, manifest: Dict,
                          result: ManifestVerificationResult):
        """
        验证插件签名

        原型阶段：验证 signature 字段存在且格式正确。
        正式版：用 Ed25519 公钥验签。
        """
        signature = manifest.get("signature", "")
        client_env = manifest.get("client_env", {})
        plugin_signature = client_env.get("plugin_signature", "")

        if not signature:
            result.warnings.append("No manifest signature found")
            result.checks["signature_valid"] = False
            return

        # 原型阶段：简单验证签名格式（base64，长度合理）
        # 正式版应实现: Ed25519.verify(public_key, manifest_bytes, signature)
        try:
            import base64
            decoded = base64.b64decode(signature)
            if len(decoded) >= 32:  # Ed25519 签名是 64 字节
                result.checks["signature_valid"] = True
            else:
                result.warnings.append("Signature too short")
                result.checks["signature_valid"] = False
        except Exception:
            result.warnings.append("Invalid signature format")
            result.checks["signature_valid"] = False

    def _verify_version(self, manifest: Dict,
                        result: ManifestVerificationResult):
        """验证插件版本是否受支持"""
        client_env = manifest.get("client_env", {})
        plugin_name = client_env.get("plugin_name", manifest.get("scanned_by", ""))
        plugin_version = client_env.get("plugin_version", manifest.get("plugin_version", ""))

        if not plugin_version:
            result.warnings.append("No plugin version specified")
            result.checks["version_supported"] = False
            return

        supported = self.supported_versions.get(plugin_name, [])
        if not supported:
            result.warnings.append(f"Unknown plugin: {plugin_name}")
            result.checks["version_supported"] = False
            return

        if plugin_version in supported:
            result.checks["version_supported"] = True
        else:
            result.warnings.append(
                f"Plugin version {plugin_version} not in supported list: {supported}")
            result.checks["version_supported"] = False

    def _verify_timestamp(self, manifest: Dict,
                          result: ManifestVerificationResult):
        """验证时间戳是否在有效期内（防重放）"""
        scanned_at = manifest.get("scanned_at", "")
        if not scanned_at:
            result.warnings.append("No scanned_at timestamp")
            result.checks["timestamp_fresh"] = False
            return

        try:
            scan_time = datetime.fromisoformat(scanned_at.replace("Z", "+00:00"))
            # 转换为本地时间比较
            if scan_time.tzinfo:
                scan_time = scan_time.replace(tzinfo=None)
            age = (datetime.now() - scan_time).total_seconds()

            if age < 0:
                result.warnings.append("scanned_at is in the future")
                result.checks["timestamp_fresh"] = False
            elif age > self.manifest_ttl:
                result.warnings.append(
                    f"Manifest expired: age={age/3600:.1f}h, ttl={self.manifest_ttl/3600:.0f}h")
                result.checks["timestamp_fresh"] = False
            else:
                result.checks["timestamp_fresh"] = True
        except (ValueError, TypeError) as e:
            result.warnings.append(f"Invalid timestamp: {e}")
            result.checks["timestamp_fresh"] = False

    def _verify_file_hashes(self, manifest: Dict,
                            result: ManifestVerificationResult):
        """抽样验证文件 SHA256"""
        files = manifest.get("files", [])
        if not files:
            result.warnings.append("No files in manifest")
            result.checks["hash_sample_passed"] = True  # 空清单不视为失败
            return

        # 根据敏感度级别决定抽样率
        sensitivity = manifest.get("overall_sensitivity", "low")
        sample_rate = (HIGH_SENSITIVITY_SAMPLE_RATE
                       if sensitivity in ("high", "critical")
                       else self.sample_rate)

        # 确定抽样数量
        import random
        sample_count = max(1, int(len(files) * sample_rate))
        sample_files = random.sample(files, min(sample_count, len(files)))

        passed = 0
        for file_entry in sample_files:
            file_path = os.path.join(self.data_dir, file_entry["path"])
            expected_hash = file_entry.get("sha256", "")

            if not os.path.exists(file_path):
                result.warnings.append(f"File not found: {file_entry['path']}")
                continue

            actual_hash = self._sha256_file(file_path)
            if actual_hash == expected_hash:
                passed += 1
            else:
                result.warnings.append(
                    f"Hash mismatch: {file_entry['path']} "
                    f"(expected {expected_hash[:16]}..., got {actual_hash[:16]}...)")

        result.files_verified = passed
        if passed == len(sample_files):
            result.checks["hash_sample_passed"] = True
        else:
            result.checks["hash_sample_passed"] = False
            result.errors.append(
                f"Hash verification failed: {passed}/{len(sample_files)} samples passed")

    def _sha256_file(self, filepath: str, chunk_size: int = 8192) -> str:
        """计算文件 SHA256"""
        sha256 = hashlib.sha256()
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                sha256.update(chunk)
        return sha256.hexdigest()

    def _calculate_trust_score(self, result: ManifestVerificationResult):
        """计算信任分和信任级别"""
        score = 0

        # 各项权重
        if result.checks["format_valid"]:
            score += 20
        if result.checks["signature_valid"]:
            score += 30
        if result.checks["version_supported"]:
            score += 15
        if result.checks["timestamp_fresh"]:
            score += 15
        if result.checks["hash_sample_passed"]:
            score += 20

        # 有错误扣分
        score -= len(result.errors) * 10
        score = max(0, min(100, score))

        result.trust_score = score

        if score >= TRUST_HIGH_THRESHOLD and not result.errors:
            result.trust_level = "high"
            result.trusted = True
        elif score >= TRUST_MEDIUM_THRESHOLD:
            result.trust_level = "medium"
            result.trusted = False
        else:
            result.trust_level = "low"
            result.trusted = False

    def get_recommended_sample_rate(self, trust_level: str) -> float:
        """根据信任级别获取推荐的 Hub 侧内容扫描抽样率"""
        if trust_level == "high":
            return 0.05  # 5%
        elif trust_level == "medium":
            return 0.30  # 30%
        else:
            return 1.0  # 100% 全量扫描


def main():
    """命令行测试"""
    import argparse
    parser = argparse.ArgumentParser(description="Manifest 验证器测试")
    parser.add_argument("--data-dir", required=True, help="数据集目录")
    parser.add_argument("--manifest", default="", help="manifest 文件路径（默认 data-dir/.ocr_manifest.json）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    verifier = ManifestVerifier(data_dir=args.data_dir)
    result = verifier.verify(args.manifest or None)

    print("\n" + "=" * 50)
    print("Manifest 验证结果")
    print("=" * 50)
    print(f"信任级别: {result.trust_level} ({result.trust_score}/100)")
    print(f"是否信任: {'是' if result.trusted else '否'}")
    print(f"综合敏感度: {result.overall_sensitivity}")
    print(f"文件验证: {result.files_verified}/{result.files_total}")
    print("\n检查项:")
    for check, passed in result.checks.items():
        print(f"  {'✅' if passed else '❌'} {check}")
    if result.errors:
        print("\n错误:")
        for e in result.errors:
            print(f"  ❌ {e}")
    if result.warnings:
        print("\n警告:")
        for w in result.warnings:
            print(f"  ⚠️  {w}")
    print(f"\n推荐 Hub 扫描抽样率: {verifier.get_recommended_sample_rate(result.trust_level)*100:.0f}%")


if __name__ == "__main__":
    main()
