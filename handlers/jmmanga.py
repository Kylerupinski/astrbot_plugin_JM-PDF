import asyncio
import shutil
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..cells.converter import Converter
from ..cells.downloader import run_download_chapter_with_hard_timeout
from ..utils.filehandler import FileHandler
from ..utils.msg_recall import MsgRecall
from .reply_helper import emit_plain_with_optional_recall


def _cleanup_partial_download(download_base_dir: str, manga_id: str, chapter_id: int):
    """下载超时后清理半成品章节目录，避免占用磁盘并干扰下次任务。"""
    manga_dir = Path(download_base_dir) / manga_id
    target_dir = manga_dir / str(chapter_id)

    if not target_dir.exists():
        logger.debug("[JM PDF plugin] 超时清理跳过，章节目录不存在: %s", target_dir)
        return

    if not target_dir.is_dir():
        logger.warning("[JM PDF plugin] 超时清理跳过，章节目标不是目录: %s", target_dir)
        return

    try:
        shutil.rmtree(target_dir)
        logger.info("[JM PDF plugin] 下载超时后已清理半成品章节目录: %s", target_dir)

        # 若漫画目录已空，顺便删除外层目录，保持缓存目录整洁。
        if manga_dir.exists() and manga_dir.is_dir():
            try:
                next(manga_dir.iterdir())
            except StopIteration:
                manga_dir.rmdir()
                logger.debug("[JM PDF plugin] 漫画目录为空，已一并清理: %s", manga_dir)
    except Exception as e:
        logger.warning("[JM PDF plugin] 下载超时后清理半成品章节目录失败: %s, err=%s", target_dir, e)

async def jmManga(
    event: AstrMessageEvent,
    args: list,
    jm_config: dict,
    download_base_dir: str,
    fallback_send_path: bool,
    recall_seconds: int,
    recall_get_notice: bool,
    recall_get_file: bool,
    download_timeout_seconds: int,
    msg_recall: MsgRecall,
):
    '''处理/jm [ID] [CHAPTER]'''
    logger.info("[JM PDF plugin] 收到下载请求: args=%s", args)
    if not args:
        yield event.plain_result("参数错误，请使用 /jm 123456 或 /jm 123456 2")
        return

    manga_id, chapter_id = args
    
    chapter_id = int(chapter_id) if chapter_id else 1
    logger.debug(
        "[JM PDF plugin] 解析参数完成: manga_id=%s, chapter_id=%s, download_base_dir=%s, download_timeout_seconds=%s",
        manga_id,
        chapter_id,
        download_base_dir,
        download_timeout_seconds,
    )
    if chapter_id <= 0:
        yield event.plain_result("章节号必须大于 0")
        return
    
    notice_text = (
        f"正在将 jm{manga_id} 的第 {chapter_id} 章转换为 PDF...\n"
        "可能需要 10s 到 1min，请耐心等待"
    )
    notice_result = await emit_plain_with_optional_recall(
        event=event,
        text=notice_text,
        recall_seconds=recall_seconds,
        recall_enabled=recall_get_notice,
        msg_recall=msg_recall,
        scene="get_notice",
    )
    if notice_result is not None:
        yield notice_result
    
    '''下载漫画'''
    logger.info("[JM PDF plugin] 开始下载阶段: manga_id=%s", manga_id)
    status, err, timed_out = await asyncio.to_thread(
        run_download_chapter_with_hard_timeout,
        manga_id,
        chapter_id,
        jm_config,
        download_base_dir,
        download_timeout_seconds,
    )

    if timed_out:
        logger.warning(
            "[JM PDF plugin] 下载阶段超时(已强制中止): manga_id=%s, timeout=%s 秒",
            manga_id,
            download_timeout_seconds,
        )

        await asyncio.to_thread(
            _cleanup_partial_download,
            download_base_dir,
            manga_id,
            chapter_id,
        )

        yield event.plain_result(
            f"下载超时（>{download_timeout_seconds} 秒），已强制中止下载进程。\n"
            "已自动清理本次下载的临时目录。\n"
            "可能原因：网络波动、域名不可达、目标资源受限。\n"
            "建议：更换 jm_config.client_api_domains，或稍后重试。"
        )
        return
    if status != 0:
        logger.warning("[JM PDF plugin] 下载阶段失败: manga_id=%s, status=%s, err=%s", manga_id, status, err)
        yield event.plain_result(err)
        return
    logger.info("[JM PDF plugin] 下载阶段完成: manga_id=%s", manga_id)
    
    '''转换PDF'''
    converter = Converter(manga_id, base_dir=download_base_dir)
    logger.info("[JM PDF plugin] 开始转换阶段: manga_id=%s, chapter_id=%s", manga_id, chapter_id)
    status, err = await asyncio.to_thread(converter.manga2Pdf, chapter_id)
    if status != 0:
        logger.warning(
            "[JM PDF plugin] 转换阶段失败: manga_id=%s, chapter_id=%s, status=%s, err=%s",
            manga_id,
            chapter_id,
            status,
            err,
        )
        yield event.plain_result(err)
        return
    logger.info("[JM PDF plugin] 转换阶段完成: manga_id=%s, chapter_id=%s", manga_id, chapter_id)
    
    '''发送PDF'''
    filehandler = FileHandler(download_base_dir, manga_id, chapter_id)
    pdf_path = filehandler.get_file_path()
    logger.debug("[JM PDF plugin] 发送文件路径: %s", pdf_path)

    try:
        ret = await msg_recall.send_file(event, pdf_path, filehandler.name)
        logger.info("[JM PDF plugin] 文件发送成功: %s", pdf_path)
    except Exception as e:
        logger.warning(f"[JM PDF plugin] 文件发送失败: {e}")
        if fallback_send_path:
            yield event.plain_result(f"文件发送失败，可手动获取: {pdf_path}")
        else:
            yield event.plain_result("文件发送失败")
        return

    # 撤回失败不应影响已发送成功的结果。
    if recall_get_file and recall_seconds > 0:
        try:
            msg_recall.schedule_recall(
                event,
                ret,
                recall_seconds,
                "get_file",
                fallback_file_name=filehandler.name,
                enable_file_delete_fallback=True,
            )
        except Exception as e:
            logger.warning("[JM PDF plugin] 创建撤回任务失败(不影响发送成功): %s", e)
