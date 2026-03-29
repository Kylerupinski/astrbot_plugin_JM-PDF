import asyncio

from astrbot.api.event import AstrMessageEvent

from ..utils.rankhandler import rankHandler
from ..utils.msg_recall import MsgRecall
from .reply_helper import emit_plain_with_optional_recall


async def jmRank(
    event: AstrMessageEvent,
    args: list,
    jm_config: dict,
    recall_seconds: int,
    recall_rank_messages: bool,
    query_timeout_seconds: int,
    msg_recall: MsgRecall,
):
    '''处理/jm rank'''
    if not args:
        yield event.plain_result("参数错误，请使用 /jm rank week 或 /jm rank month")
        return

    duration, = args
    if not duration:
        duration = 'week'
    status_text = "正在获取排行榜中，请稍候"
    status_result = await emit_plain_with_optional_recall(
        event=event,
        text=status_text,
        recall_seconds=recall_seconds,
        recall_enabled=recall_rank_messages,
        msg_recall=msg_recall,
        scene="rank_status",
    )
    if status_result is not None:
        yield status_result
    
    '''获取排行榜结果'''
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(rankHandler, jm_config, duration),
            timeout=max(1, int(query_timeout_seconds)),
        )
        result_msg = await emit_plain_with_optional_recall(
            event=event,
            text=result,
            recall_seconds=recall_seconds,
            recall_enabled=recall_rank_messages,
            msg_recall=msg_recall,
            scene="rank_result",
        )
        if result_msg is not None:
            yield result_msg
    except asyncio.TimeoutError:
        timeout_text = "获取排行榜超时，可能是网络连接问题，请稍后重试"
        timeout_msg = await emit_plain_with_optional_recall(
            event=event,
            text=timeout_text,
            recall_seconds=recall_seconds,
            recall_enabled=recall_rank_messages,
            msg_recall=msg_recall,
            scene="rank_timeout",
        )
        if timeout_msg is not None:
            yield timeout_msg
    except Exception as e:
        err_text = f"获取排行榜失败: {e}"
        err_msg = await emit_plain_with_optional_recall(
            event=event,
            text=err_text,
            recall_seconds=recall_seconds,
            recall_enabled=recall_rank_messages,
            msg_recall=msg_recall,
            scene="rank_error",
        )
        if err_msg is not None:
            yield err_msg