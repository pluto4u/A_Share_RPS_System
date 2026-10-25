"""
火山引擎 TOS 对象存储管理器

提供数据归档（上传）、恢复（下载）、同步等功能。
TOS Bucket 作为数据中枢，本地和云端均可读写。

读写权限规则:
  - cloud_ecs:     主写入方，自由读写，不需确认
  - local_laptop:  默认只读消费方，写入需通过冲突检测或 --force 确认

CLI:
    python tos_storage.py upload              # 上传全部本地数据到 TOS
    python tos_storage.py download            # 从 TOS 下载全部数据到本地
    python tos_storage.py sync-up             # 增量上传（含冲突检测）
    python tos_storage.py sync-down           # 增量下载
    python tos_storage.py sync-up --force     # 增量上传，强制覆盖冲突
    python tos_storage.py status              # 查看 TOS 与本地数据状态 + 文件归属
    python tos_storage.py ls [prefix]         # 列出 TOS 上的文件
"""

from pathlib import Path as _BootstrapPath
import sys as _bootstrap_sys
_BOOTSTRAP_ROOT = _BootstrapPath(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in _bootstrap_sys.path:
    _bootstrap_sys.path.insert(0, str(_BOOTSTRAP_ROOT))
from project_paths import PROJECT_ROOT, ensure_script_imports, script_file
ensure_script_imports()

import json
import logging
import os
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "tos_config.json"
PROJECT_ROOT = PROJECT_ROOT

MANIFEST_KEY = "_sync_manifest.json"

SYNC_DIRS: List[Dict] = [
    # Keep the remote prefixes stable so existing cloud archives remain readable.
    {"local": "data/market", "remote": "backtest/data", "patterns": ["*.parquet", "*.csv", "*.json"]},
    {"local": "data/market/incremental_updates", "remote": "backtest/data/incremental_updates", "patterns": ["*.parquet"]},
    {"local": "outputs/reports/emotion_validation", "remote": "backtest/reports/emotion_validation", "patterns": ["*.csv", "*.png", "*.txt"]},
    {"local": "outputs/reports/etf_rotation", "remote": "backtest/reports/etf_rotation", "patterns": ["*.csv", "*.png", "*.txt"]},
    {"local": "outputs/signals", "remote": "output", "patterns": ["*.csv", "*.txt"]},
]

VALID_ROLES = ("cloud_ecs", "local_laptop")


CONFIG_EXAMPLE = PROJECT_ROOT / "tos_config.json.example"


def _load_config() -> dict:
    cfg = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    for key in ("access_key", "secret_key", "endpoint", "region", "bucket"):
        val = os.environ.get(f"TOS_{key.upper()}", cfg.get(key, ""))
        cfg[key] = val
        if not val:
            raise ValueError(f"TOS 配置缺失: {key}（可在 tos_config.json 或环境变量 TOS_{key.upper()} 中设置）")

    cfg["role"] = os.environ.get("TOS_ROLE", cfg.get("role", "local_laptop"))
    if cfg["role"] not in VALID_ROLES:
        raise ValueError(f"TOS role 无效: {cfg['role']}，必须为 {VALID_ROLES} 之一")

    return cfg


# ======================================================================
# SyncManifest - 多端写入追踪
# ======================================================================

class SyncManifest:
    """
    在 TOS Bucket 根目录维护 _sync_manifest.json，
    记录每个文件的最后写入者 (role) 和时间，用于冲突检测。
    """

    def __init__(self, storage: "TosStorage"):
        self._storage = storage
        self._data: Dict[str, dict] = {}
        self._dirty = False

    def load(self):
        """从 TOS 下载 manifest"""
        try:
            resp = self._storage.client.get_object(self._storage.bucket, MANIFEST_KEY)
            raw = resp.read().decode("utf-8")
            self._data = json.loads(raw)
            logger.debug(f"[Manifest] 已加载, {len(self._data)} 条记录")
        except Exception:
            self._data = {}
            logger.debug("[Manifest] TOS 上无 manifest 或读取失败, 使用空 manifest")

    def save(self):
        """将 manifest 上传到 TOS"""
        if not self._dirty:
            return
        try:
            content = json.dumps(self._data, ensure_ascii=False, indent=2, default=str)
            self._storage.client.put_object(
                self._storage.bucket, MANIFEST_KEY, content=content,
            )
            self._dirty = False
            logger.debug(f"[Manifest] 已保存, {len(self._data)} 条记录")
        except Exception as e:
            logger.error(f"[Manifest] 保存失败: {e}")

    def record_write(self, key: str, role: str, size: int):
        """记录一次写入"""
        self._data[key] = {
            "writer": role,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "size": size,
        }
        self._dirty = True

    def check_conflict(self, key: str, my_role: str) -> Optional[dict]:
        """
        检查是否存在冲突。

        返回 None 表示无冲突，可直接写入。
        返回 dict 表示有冲突 (包含 writer, timestamp 信息)。

        规则:
          - cloud_ecs 写入不检测冲突（主写入方）
          - local_laptop 写入时, 如果文件由 cloud_ecs 最后写入, 则报冲突
        """
        if my_role == "cloud_ecs":
            entry = self._data.get(key)
            if entry and entry.get("writer") == "local_laptop":
                logger.info(f"[Manifest] 覆盖 local_laptop 的修改: {key}")
            return None

        entry = self._data.get(key)
        if not entry:
            return None

        if entry.get("writer") == my_role:
            return None

        return entry

    def get_entry(self, key: str) -> Optional[dict]:
        """获取单个文件的 manifest 记录"""
        return self._data.get(key)

    @property
    def entries(self) -> Dict[str, dict]:
        return self._data


class TosStorage:
    """火山引擎 TOS 对象存储管理器"""

    def __init__(self, config: dict = None):
        try:
            import tos as tos_sdk
        except ImportError:
            raise ImportError("请先安装 TOS SDK: pip install tos")

        self._tos = tos_sdk
        cfg = config or _load_config()
        self.bucket = cfg["bucket"]
        self.role = cfg.get("role", "local_laptop")
        self.client = tos_sdk.TosClientV2(
            ak=cfg["access_key"],
            sk=cfg["secret_key"],
            endpoint=cfg["endpoint"],
            region=cfg["region"],
        )
        self._manifest: Optional[SyncManifest] = None

    def _get_manifest(self) -> SyncManifest:
        """懒加载 manifest"""
        if self._manifest is None:
            self._manifest = SyncManifest(self)
            self._manifest.load()
        return self._manifest

    # ------------------------------------------------------------------
    # 基础操作
    # ------------------------------------------------------------------

    def upload_file(self, local_path: str, remote_key: str) -> bool:
        """上传单个文件到 TOS"""
        try:
            self.client.put_object_from_file(self.bucket, remote_key, local_path)
            size = os.path.getsize(local_path)
            size_mb = size / 1024 / 1024
            logger.info(f"[TOS] 已上传: {remote_key} ({size_mb:.1f} MB) [by {self.role}]")
            self._get_manifest().record_write(remote_key, self.role, size)
            return True
        except Exception as e:
            logger.error(f"[TOS] 上传失败 {remote_key}: {e}")
            return False

    def download_file(self, remote_key: str, local_path: str) -> bool:
        """从 TOS 下载单个文件"""
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            self.client.get_object_to_file(self.bucket, remote_key, local_path)
            size_mb = os.path.getsize(local_path) / 1024 / 1024
            logger.info(f"[TOS] 已下载: {remote_key} -> {local_path} ({size_mb:.1f} MB)")
            return True
        except Exception as e:
            logger.error(f"[TOS] 下载失败 {remote_key}: {e}")
            return False

    def list_objects(self, prefix: str = "", max_keys: int = 1000) -> List[dict]:
        """列出 TOS 上指定前缀的所有对象"""
        objects = []
        continuation_token = ""
        try:
            while True:
                if continuation_token:
                    resp = self.client.list_objects_type2(
                        self.bucket, prefix=prefix, max_keys=max_keys,
                        continuation_token=continuation_token,
                    )
                else:
                    resp = self.client.list_objects_type2(
                        self.bucket, prefix=prefix, max_keys=max_keys,
                    )

                for obj in resp.contents:
                    objects.append({
                        "key": obj.key,
                        "size": obj.size,
                        "last_modified": obj.last_modified,
                    })

                if not resp.is_truncated:
                    break
                continuation_token = resp.next_continuation_token

        except Exception as e:
            logger.error(f"[TOS] 列举对象失败 (prefix={prefix}): {e}")
        return objects

    def head_object(self, remote_key: str) -> Optional[dict]:
        """获取远端对象元信息（大小、修改时间）"""
        try:
            resp = self.client.head_object(self.bucket, remote_key)
            return {
                "key": remote_key,
                "size": resp.content_length,
                "last_modified": resp.last_modified,
            }
        except self._tos.exceptions.TosServerError as e:
            if e.status_code == 404:
                return None
            raise
        except Exception:
            return None

    def delete_object(self, remote_key: str) -> bool:
        """删除 TOS 上的单个对象"""
        try:
            self.client.delete_object(self.bucket, remote_key)
            logger.info(f"[TOS] 已删除: {remote_key}")
            return True
        except Exception as e:
            logger.error(f"[TOS] 删除失败 {remote_key}: {e}")
            return False

    # ------------------------------------------------------------------
    # 批量同步
    # ------------------------------------------------------------------

    def _collect_local_files(self, local_dir: Path, patterns: List[str]) -> Dict[str, Tuple[float, int]]:
        """收集本地目录中匹配的文件及其 mtime 和 size"""
        result = {}
        if not local_dir.exists():
            return result
        for pattern in patterns:
            for f in local_dir.glob(pattern):
                if f.is_file():
                    rel = f.relative_to(local_dir).as_posix()
                    result[rel] = (f.stat().st_mtime, f.stat().st_size)
        return result

    @staticmethod
    def _remote_key(prefix: str, relative_path: str) -> str:
        return f"{prefix.rstrip('/')}/{relative_path.lstrip('/')}"

    @staticmethod
    def _relative_remote_key(prefix: str, remote_key: str) -> str:
        normalized = prefix.rstrip('/') + "/"
        return remote_key[len(normalized):] if remote_key.startswith(normalized) else remote_key

    def _collect_remote_files(self, prefix: str) -> Dict[str, Tuple[float, int]]:
        """收集 TOS 上指定前缀的文件及其 mtime 和 size"""
        result = {}
        for obj in self.list_objects(prefix=prefix):
            key = obj["key"]
            if key.endswith("/"):
                continue
            mtime = 0.0
            if obj.get("last_modified"):
                try:
                    dt = datetime.fromisoformat(str(obj["last_modified"]).replace("Z", "+00:00"))
                    mtime = dt.timestamp()
                except Exception:
                    pass
            result[key] = (mtime, obj["size"])
        return result

    def upload_all(self, dry_run: bool = False, force: bool = False) -> dict:
        """上传全部本地数据到 TOS（全量覆盖）"""
        stats = {"uploaded": 0, "skipped": 0, "failed": 0, "conflict": 0, "total_mb": 0.0}
        manifest = self._get_manifest()

        for sync_cfg in SYNC_DIRS:
            local_dir = PROJECT_ROOT / sync_cfg["local"]
            remote_prefix = sync_cfg["remote"]
            local_files = self._collect_local_files(local_dir, sync_cfg["patterns"])

            for rel_path, (_, size) in local_files.items():
                remote_key = self._remote_key(remote_prefix, rel_path)

                conflict = manifest.check_conflict(remote_key, self.role)
                if conflict and not force:
                    logger.warning(
                        f"[冲突] {remote_key} 由 {conflict['writer']} "
                        f"在 {conflict['timestamp']} 写入, 跳过 (使用 --force 覆盖)"
                    )
                    stats["conflict"] += 1
                    continue

                abs_path = str(local_dir / rel_path)

                if dry_run:
                    tag = "[FORCE] " if conflict else ""
                    logger.info(f"[DRY] {tag}将上传: {rel_path} ({size / 1024 / 1024:.1f} MB)")
                    stats["skipped"] += 1
                    continue

                if self.upload_file(abs_path, remote_key):
                    stats["uploaded"] += 1
                    stats["total_mb"] += size / 1024 / 1024
                else:
                    stats["failed"] += 1

        manifest.save()
        return stats

    def download_all(self, dry_run: bool = False) -> dict:
        """从 TOS 下载全部数据到本地（全量覆盖）"""
        stats = {"downloaded": 0, "skipped": 0, "failed": 0, "total_mb": 0.0}

        for sync_cfg in SYNC_DIRS:
            local_dir = PROJECT_ROOT / sync_cfg["local"]
            remote_prefix = sync_cfg["remote"]
            remote_files = self._collect_remote_files(remote_prefix + "/")

            for remote_key, (_, size) in remote_files.items():
                rel_path = self._relative_remote_key(remote_prefix, remote_key)
                local_path = str(local_dir / rel_path)

                if dry_run:
                    logger.info(f"[DRY] 将下载: {remote_key} ({size / 1024 / 1024:.1f} MB)")
                    stats["skipped"] += 1
                    continue

                if self.download_file(remote_key, local_path):
                    stats["downloaded"] += 1
                    stats["total_mb"] += size / 1024 / 1024
                else:
                    stats["failed"] += 1

        return stats

    def sync_upload(self, dry_run: bool = False, force: bool = False) -> dict:
        """
        增量上传: 仅上传本地比 TOS 更新（或 TOS 不存在）的文件。
        含冲突检测: local_laptop 上传 cloud_ecs 写入的文件需 --force。
        """
        stats = {"uploaded": 0, "skipped": 0, "failed": 0, "conflict": 0, "total_mb": 0.0}
        manifest = self._get_manifest()

        for sync_cfg in SYNC_DIRS:
            local_dir = PROJECT_ROOT / sync_cfg["local"]
            remote_prefix = sync_cfg["remote"]
            local_files = self._collect_local_files(local_dir, sync_cfg["patterns"])
            remote_files = self._collect_remote_files(remote_prefix + "/")

            for rel_path, (local_mtime, local_size) in local_files.items():
                remote_key = self._remote_key(remote_prefix, rel_path)
                remote_info = remote_files.get(remote_key)

                if remote_info:
                    remote_mtime, remote_size = remote_info
                    if local_size == remote_size and local_mtime <= remote_mtime:
                        stats["skipped"] += 1
                        continue

                conflict = manifest.check_conflict(remote_key, self.role)
                if conflict and not force:
                    logger.warning(
                        f"[冲突] {remote_key} 由 {conflict['writer']} "
                        f"在 {conflict['timestamp']} 写入, 跳过 (使用 --force 覆盖)"
                    )
                    stats["conflict"] += 1
                    continue

                if dry_run:
                    tag = "[FORCE] " if conflict else ""
                    logger.info(f"[DRY] {tag}将上传: {rel_path} ({local_size / 1024 / 1024:.1f} MB)")
                    stats["skipped"] += 1
                    continue

                abs_path = str(local_dir / rel_path)
                if self.upload_file(abs_path, remote_key):
                    stats["uploaded"] += 1
                    stats["total_mb"] += local_size / 1024 / 1024
                else:
                    stats["failed"] += 1

        manifest.save()
        return stats

    def sync_download(self, dry_run: bool = False) -> dict:
        """增量下载: 仅下载 TOS 上比本地更新（或本地不存在）的文件。"""
        stats = {"downloaded": 0, "skipped": 0, "failed": 0, "total_mb": 0.0}

        for sync_cfg in SYNC_DIRS:
            local_dir = PROJECT_ROOT / sync_cfg["local"]
            remote_prefix = sync_cfg["remote"]
            local_files = self._collect_local_files(local_dir, sync_cfg["patterns"])
            remote_files = self._collect_remote_files(remote_prefix + "/")

            for remote_key, (remote_mtime, remote_size) in remote_files.items():
                rel_path = self._relative_remote_key(remote_prefix, remote_key)
                local_info = local_files.get(rel_path)

                if local_info:
                    local_mtime, local_size = local_info
                    if local_size == remote_size and local_mtime >= remote_mtime:
                        stats["skipped"] += 1
                        continue

                local_path = str(local_dir / rel_path)

                if dry_run:
                    logger.info(f"[DRY] 将下载: {remote_key} ({remote_size / 1024 / 1024:.1f} MB)")
                    stats["skipped"] += 1
                    continue

                if self.download_file(remote_key, local_path):
                    stats["downloaded"] += 1
                    stats["total_mb"] += remote_size / 1024 / 1024
                else:
                    stats["failed"] += 1

        return stats

    # ------------------------------------------------------------------
    # 状态查看
    # ------------------------------------------------------------------

    def status(self) -> dict:
        """比较本地与 TOS 的数据状态，含文件归属信息"""
        manifest = self._get_manifest()
        report = {}

        for sync_cfg in SYNC_DIRS:
            label = sync_cfg["local"]
            local_dir = PROJECT_ROOT / sync_cfg["local"]
            remote_prefix = sync_cfg["remote"]

            local_files = self._collect_local_files(local_dir, sync_cfg["patterns"])
            remote_files = self._collect_remote_files(remote_prefix + "/")

            local_keys = {self._remote_key(remote_prefix, key) for key in local_files}
            remote_keys = set(remote_files.keys())

            local_total_mb = sum(s for _, s in local_files.values()) / 1024 / 1024
            remote_total_mb = sum(s for _, s in remote_files.values()) / 1024 / 1024

            writers = {"cloud_ecs": 0, "local_laptop": 0, "unknown": 0}
            all_keys = local_keys | remote_keys
            for key in all_keys:
                entry = manifest.get_entry(key)
                if entry:
                    w = entry.get("writer", "unknown")
                    writers[w] = writers.get(w, 0) + 1
                else:
                    writers["unknown"] += 1

            report[label] = {
                "local_files": len(local_files),
                "local_size_mb": round(local_total_mb, 1),
                "remote_files": len(remote_files),
                "remote_size_mb": round(remote_total_mb, 1),
                "only_local": len(local_keys - remote_keys),
                "only_remote": len(remote_keys - local_keys),
                "both": len(local_keys & remote_keys),
                "writers": writers,
            }

        return report

    def verify_recent_updates(self, top_n: int = 20) -> List[dict]:
        """
        收集 TOS 上所有同步目录中的文件，按 last_modified 降序，返回最近更新的 top_n 条。
        用于确认云端数据是否在近期有写入。
        返回项: {"key", "size", "last_modified", "last_modified_iso", "writer"}
        """
        all_objs: List[dict] = []
        for sync_cfg in SYNC_DIRS:
            prefix = sync_cfg["remote"].rstrip("/") + "/"
            for obj in self.list_objects(prefix=prefix):
                if obj.get("key", "").endswith("/"):
                    continue
                all_objs.append(obj)

        def _ts(o):
            lm = o.get("last_modified")
            if lm is None:
                return 0.0
            try:
                if hasattr(lm, "timestamp"):
                    return lm.timestamp()
                s = str(lm).replace("Z", "+00:00")
                return datetime.fromisoformat(s).timestamp()
            except Exception:
                return 0.0

        all_objs.sort(key=_ts, reverse=True)
        manifest = self._get_manifest()
        result = []
        for o in all_objs[:top_n]:
            key = o["key"]
            entry = manifest.get_entry(key)
            writer = entry.get("writer", "?") if entry else "?"
            ts = _ts(o)
            last_iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else ""
            result.append({
                "key": key,
                "size": o["size"],
                "last_modified": ts,
                "last_modified_iso": last_iso,
                "writer": writer,
            })
        return result


# ======================================================================
# CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="火山引擎 TOS 对象存储 - 数据归档与同步",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python tos_storage.py upload              # 全量上传
    python tos_storage.py download            # 全量下载
    python tos_storage.py sync-up             # 增量上传 (含冲突检测)
    python tos_storage.py sync-up --force     # 增量上传, 强制覆盖冲突
    python tos_storage.py sync-down           # 增量下载
    python tos_storage.py sync-up --dry-run   # 预览将要上传的文件
    python tos_storage.py status              # 查看状态 + 文件归属
    python tos_storage.py verify              # 确认云端数据是否近期有更新
    python tos_storage.py ls                  # 列出 TOS 全部文件
    python tos_storage.py ls backtest/data    # TOS 远端仍沿用历史前缀

角色说明:
    cloud_ecs     云端服务器 (主写入方, 自由读写)
    local_laptop  本地笔记本 (默认只读, 写入需 --force 确认冲突)
    当前角色由 tos_config.json 中的 "role" 字段或环境变量 TOS_ROLE 决定。
""",
    )

    parser.add_argument(
        "action",
        choices=["upload", "download", "sync-up", "sync-down", "status", "verify", "ls"],
        help="操作类型",
    )
    parser.add_argument("prefix", nargs="?", default="", help="ls 命令的前缀过滤")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际执行")
    parser.add_argument("--force", action="store_true", help="强制覆盖冲突文件 (仅 upload/sync-up)")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    storage = TosStorage()
    t0 = time.time()

    print(f"当前角色: {storage.role}")

    if args.action == "upload":
        print("=== 全量上传本地数据到 TOS ===")
        stats = storage.upload_all(dry_run=args.dry_run, force=args.force)
        print(f"\n上传完成: {stats['uploaded']} 文件, {stats['total_mb']:.1f} MB, "
              f"跳过: {stats['skipped']}, 冲突: {stats['conflict']}, 失败: {stats['failed']}")

    elif args.action == "download":
        print("=== 全量从 TOS 下载数据到本地 ===")
        stats = storage.download_all(dry_run=args.dry_run)
        print(f"\n下载完成: {stats['downloaded']} 文件, {stats['total_mb']:.1f} MB, "
              f"跳过: {stats['skipped']}, 失败: {stats['failed']}")

    elif args.action == "sync-up":
        print("=== 增量上传到 TOS ===")
        stats = storage.sync_upload(dry_run=args.dry_run, force=args.force)
        print(f"\n同步上传完成: {stats['uploaded']} 文件, {stats['total_mb']:.1f} MB, "
              f"跳过: {stats['skipped']}, 冲突: {stats['conflict']}, 失败: {stats['failed']}")

    elif args.action == "sync-down":
        print("=== 增量从 TOS 下载 ===")
        stats = storage.sync_download(dry_run=args.dry_run)
        print(f"\n同步下载完成: {stats['downloaded']} 文件, {stats['total_mb']:.1f} MB, "
              f"跳过: {stats['skipped']}, 失败: {stats['failed']}")

    elif args.action == "status":
        print("=== 数据状态对比 ===\n")
        report = storage.status()
        for label, info in report.items():
            print(f"  {label}:")
            print(f"    本地: {info['local_files']} 文件, {info['local_size_mb']:.1f} MB")
            print(f"    TOS:  {info['remote_files']} 文件, {info['remote_size_mb']:.1f} MB")
            print(f"    仅本地: {info['only_local']}, 仅TOS: {info['only_remote']}, 两端都有: {info['both']}")
            w = info["writers"]
            print(f"    归属: cloud_ecs={w.get('cloud_ecs', 0)}, "
                  f"local_laptop={w.get('local_laptop', 0)}, "
                  f"未记录={w.get('unknown', 0)}")
            print()

    elif args.action == "verify":
        print("=== 云端数据近期更新确认 ===\n")
        top_n = 20
        recent = storage.verify_recent_updates(top_n=top_n)
        if not recent:
            print("  TOS 上暂无同步目录中的文件，请先执行 sync-up 或 pipeline --tos 上传数据。")
        else:
            print(f"  以下为 TOS 上最近更新的 {min(top_n, len(recent))} 个文件（按修改时间倒序）：\n")
            for i, r in enumerate(recent, 1):
                size_mb = r["size"] / 1024 / 1024
                print(f"  {i:2}. {r['last_modified_iso']}  {r['writer']:>13}  {size_mb:6.2f} MB  {r['key']}")
            latest = recent[0]
            print(f"\n  最新更新: {latest['last_modified_iso']} — {latest['key']} (by {latest['writer']})")
            print("  若上述时间在近期（如今日/昨日），说明云端写入正常。")

    elif args.action == "ls":
        prefix = args.prefix
        print(f"=== TOS 文件列表 (prefix={prefix or '/'}) ===\n")
        objects = storage.list_objects(prefix=prefix)
        if not objects:
            print("  (无文件)")
        else:
            manifest = storage._get_manifest()
            total_mb = 0
            for obj in objects:
                size_mb = obj["size"] / 1024 / 1024
                total_mb += size_mb
                entry = manifest.get_entry(obj["key"])
                writer = entry["writer"] if entry else "?"
                print(f"  {obj['key']:55s}  {size_mb:8.2f} MB  [{writer:>13s}]")
            print(f"\n  共 {len(objects)} 个文件, {total_mb:.1f} MB")

    elapsed = time.time() - t0
    print(f"\n耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
