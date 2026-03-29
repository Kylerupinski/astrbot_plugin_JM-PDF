import asyncio

from astrbot.api.event import AstrMessageEvent

from ..utils.cachecleaner import CacheCleaner

async def jmClear(
    event: AstrMessageEvent,
    args: list,
    download_base_dir: str,
    max_cache_count: int,
    protected_manga_ids: set[str] | None = None,
):
    _ = args
    cachecleaner = CacheCleaner(download_base_dir, max_cache_count)
    summary = await asyncio.to_thread(cachecleaner.cleanCache, True, protected_manga_ids)
    yield event.plain_result(
        "缓存清理完成\n"
        f"扫描: {summary['scanned_total']} 项\n"
        f"受保护跳过: {summary['protected_skipped']} 项\n"
        f"已删除: {summary['removed']} 项\n"
        f"删除失败: {summary['failed']} 项\n"
        f"可清理剩余: {summary['remaining_eligible']} 项"
    )
    
    