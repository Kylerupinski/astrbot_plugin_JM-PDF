from astrbot.api.event import AstrMessageEvent

from ..utils.msg_recall import MsgRecall


async def emit_plain_with_optional_recall(
    event: AstrMessageEvent,
    text: str,
    recall_seconds: int,
    recall_enabled: bool,
    msg_recall: MsgRecall,
    scene: str,
):
    """发送文本；开启撤回时走主动发送并调度撤回，否则返回 plain_result 供外层 yield。"""
    if recall_enabled and recall_seconds > 0:
        sent = await msg_recall.send_plain(event, text)
        msg_recall.schedule_recall(event, sent, recall_seconds, scene)
        return None

    return event.plain_result(text)
