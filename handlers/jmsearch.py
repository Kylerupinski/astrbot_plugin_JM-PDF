import asyncio

from astrbot.api.event import AstrMessageEvent

from ..utils.searchhandler import searchHandler
from ..utils.msg_recall import MsgRecall
from .reply_helper import emit_plain_with_optional_recall


async def jmSearch(
    event: AstrMessageEvent,
    args: list,
    jm_config: dict,
    recall_seconds: int,
    recall_search_messages: bool,
    query_timeout_seconds: int,
    msg_recall: MsgRecall,
):
    '''处理/jm search [KEYWORD]'''
    if not args:
        yield event.plain_result("参数错误，请使用 /jm search 关键词")
        return

    keyword, = args
    
    status_text = "获取搜索结果中，请稍候"
    status_result = await emit_plain_with_optional_recall(
        event=event,
        text=status_text,
        recall_seconds=recall_seconds,
        recall_enabled=recall_search_messages,
        msg_recall=msg_recall,
        scene="search_status",
    )
    if status_result is not None:
        yield status_result
    
    '''获取搜索结果'''
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(searchHandler, jm_config, keyword, "site"),
            timeout=max(1, int(query_timeout_seconds)),
        )
        result_msg = await emit_plain_with_optional_recall(
            event=event,
            text=result,
            recall_seconds=recall_seconds,
            recall_enabled=recall_search_messages,
            msg_recall=msg_recall,
            scene="search_result",
        )
        if result_msg is not None:
            yield result_msg
    except asyncio.TimeoutError:
        timeout_text = "搜索超时，可能是网络连接问题，请稍后重试"
        timeout_msg = await emit_plain_with_optional_recall(
            event=event,
            text=timeout_text,
            recall_seconds=recall_seconds,
            recall_enabled=recall_search_messages,
            msg_recall=msg_recall,
            scene="search_timeout",
        )
        if timeout_msg is not None:
            yield timeout_msg
    except Exception as e:
        err_text = f"搜索失败: {e}"
        err_msg = await emit_plain_with_optional_recall(
            event=event,
            text=err_text,
            recall_seconds=recall_seconds,
            recall_enabled=recall_search_messages,
            msg_recall=msg_recall,
            scene="search_error",
        )
        if err_msg is not None:
            yield err_msg