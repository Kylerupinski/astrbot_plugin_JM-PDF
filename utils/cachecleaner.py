import shutil
from pathlib import Path
from astrbot.api import logger

class CacheCleaner:
    def __init__(self, storage_dir: str, maxfilecount=2):
        # downloads 目录中的所有内容都视为缓存（章节目录、PDF、图片等）
        self.storage_dir = Path(storage_dir)
        self.maxfilecount = maxfilecount
    
    def cleanCache(self, force: bool = False, protected_manga_ids: set[str] | None = None) -> dict[str, int | bool]:
        '''检查并清理缓存

        Args:
            force: True 时忽略阈值，直接清理全部缓存项。
            protected_manga_ids: 正在处理中的 manga_id，清理时跳过其目录和对应 PDF。
        '''
        if not self.storage_dir.exists():
            logger.debug("[JM PDF plugin] 存储目录不存在，跳过清理: %s", self.storage_dir)
            return {
                "force": force,
                "max_cache_count": int(self.maxfilecount),
                "scanned_total": 0,
                "protected_skipped": 0,
                "eligible_total": 0,
                "remove_target": 0,
                "removed": 0,
                "failed": 0,
                "remaining_eligible": 0,
            }

        protected_manga_ids = {str(mid) for mid in (protected_manga_ids or set()) if str(mid).strip()}

        def _is_protected(entry_name: str) -> bool:
            for manga_id in protected_manga_ids:
                if entry_name == manga_id or entry_name.startswith(f"{manga_id}-"):
                    return True
            return False

        scanned_entries = [p for p in self.storage_dir.iterdir() if p.name != ".gitkeep"]
        protected_skipped = sum(1 for p in scanned_entries if _is_protected(p.name))
        entries = [p for p in scanned_entries if not _is_protected(p.name)]
        logger.debug(
            "[JM PDF plugin] 缓存扫描完成: storage_dir=%s, scanned=%s, eligible=%s, protected_skipped=%s, protected=%s, max_cache_count=%s",
            self.storage_dir,
            len(scanned_entries),
            len(entries),
            protected_skipped,
            sorted(protected_manga_ids),
            self.maxfilecount,
        )
        
        if not force and len(entries) <= self.maxfilecount:
            logger.debug("[JM PDF plugin] 缓存未超过阈值，无需清理")
            return {
                "force": force,
                "max_cache_count": int(self.maxfilecount),
                "scanned_total": len(scanned_entries),
                "protected_skipped": protected_skipped,
                "eligible_total": len(entries),
                "remove_target": 0,
                "removed": 0,
                "failed": 0,
                "remaining_eligible": len(entries),
            }

        if force:
            overflow_count = len(entries)
            logger.info(
                "[JM PDF plugin] 强制清理缓存: total=%s, remove=%s",
                len(entries),
                overflow_count,
            )
        else:
            overflow_count = len(entries) - self.maxfilecount

        entries_sorted = sorted(entries, key=lambda p: p.stat().st_mtime)
        to_remove = entries_sorted[:overflow_count]
        if not force:
            logger.info(
                "[JM PDF plugin] 缓存超限，开始清理最旧项: total=%s, keep=%s, remove=%s",
                len(entries),
                self.maxfilecount,
                overflow_count,
            )

        removed = 0
        failed = 0
        for entry in to_remove:
            try:
                if entry.is_file():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
                removed += 1
                logger.debug("[JM PDF plugin] 已删除缓存项: %s", entry)
            except PermissionError as e:
                failed += 1
                logger.warning("[JM PDF plugin] 权限错误，无法删除缓存项: %s, 错误: %s", entry, e)
            except Exception as e:
                failed += 1
                logger.warning("[JM PDF plugin] 删除缓存项失败: %s, 错误: %s", entry, e)

        remaining_eligible = max(0, len(entries) - removed)
        logger.info(
            "[JM PDF plugin] 缓存清理完成: force=%s, scanned=%s, eligible=%s, protected_skipped=%s, remove_target=%s, removed=%s, failed=%s, remaining_eligible=%s",
            force,
            len(scanned_entries),
            len(entries),
            protected_skipped,
            overflow_count,
            removed,
            failed,
            remaining_eligible,
        )
        return {
            "force": force,
            "max_cache_count": int(self.maxfilecount),
            "scanned_total": len(scanned_entries),
            "protected_skipped": protected_skipped,
            "eligible_total": len(entries),
            "remove_target": overflow_count,
            "removed": removed,
            "failed": failed,
            "remaining_eligible": remaining_eligible,
        }