import asyncio
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain


class MsgRecall:
    """统一的消息撤回服务（参考 help_typst 的 send_wait/recall 设计）。"""

    def __init__(self):
        self._tasks: list[asyncio.Task] = []

    async def terminate(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def _remove_task(self, task: asyncio.Task):
        try:
            self._tasks.remove(task)
        except ValueError:
            pass

    def _extract_message_id(self, resp: Any) -> int | str | None:
        if not resp:
            return None

        if isinstance(resp, (int, str)):
            return resp

        if isinstance(resp, dict):
            data = resp.get("data")
            if isinstance(data, dict):
                if "message_id" in data:
                    return data["message_id"]
                if "res_id" in data:
                    return data["res_id"]
                if "forward_id" in data:
                    return data["forward_id"]
                if "id" in data:
                    return data["id"]

            if "message_id" in resp:
                return resp["message_id"]
            if "id" in resp:
                return resp["id"]
            return None

        if val := getattr(resp, "message_id", None):
            return val

        if val := getattr(resp, "id", None):
            return val

        return None

    def _is_group_chat(self, event: AstrMessageEvent) -> bool:
        try:
            return bool(event.get_group_id())
        except Exception:
            return False

    async def _delete_group_file_by_name(
        self,
        event: AstrMessageEvent,
        file_name: str,
        retries: int = 3,
        interval_seconds: int = 2,
    ) -> bool:
        """参考 qqadmin：通过群文件列表按名称查 file_id，再删除群文件。"""
        if not self._is_group_chat(event):
            logger.debug("[JM PDF plugin] 私聊消息跳过群文件删除兜底: file=%s", file_name)
            return False

        bot = getattr(event, "bot", None)
        group_id = event.get_group_id() if hasattr(event, "get_group_id") else None
        if not bot or not group_id:
            logger.warning("[JM PDF plugin] 无法删除群文件（缺少 bot/group_id）: file=%s", file_name)
            return False

        if not hasattr(bot, "get_group_root_files") or not hasattr(bot, "delete_group_file"):
            logger.warning("[JM PDF plugin] 当前平台不支持群文件删除接口: file=%s", file_name)
            return False

        for i in range(max(1, retries)):
            try:
                response = await bot.get_group_root_files(group_id=int(group_id))
                files = response.get("files", []) if isinstance(response, dict) else []
                target = next((f for f in files if f.get("file_name") == file_name), None)

                if target and target.get("file_id"):
                    await bot.delete_group_file(
                        group_id=int(group_id),
                        file_id=target["file_id"],
                    )
                    logger.info(
                        "[JM PDF plugin] 已执行群文件删除兜底: group_id=%s, file=%s, file_id=%s",
                        group_id,
                        file_name,
                        target["file_id"],
                    )
                    return True
            except Exception as e:
                logger.warning(
                    "[JM PDF plugin] 删除群文件尝试失败(%s/%s): file=%s, err=%s",
                    i + 1,
                    retries,
                    file_name,
                    e,
                )

            if i < retries - 1:
                await asyncio.sleep(max(0, interval_seconds))

        logger.warning("[JM PDF plugin] 群文件删除兜底未命中目标文件: group_id=%s, file=%s", group_id, file_name)
        return False

    async def _delete_group_file_after_delay(
        self,
        event: AstrMessageEvent,
        file_name: str,
        delay: int,
    ):
        await asyncio.sleep(max(0, int(delay)))
        await self._delete_group_file_by_name(event, file_name)

    async def _recall_msg(
        self,
        event: AstrMessageEvent,
        message_id: int | str,
        delay: int,
        fallback_file_name: str | None = None,
        enable_file_delete_fallback: bool = False,
    ):
        await asyncio.sleep(max(0, int(delay)))
        try:
            if hasattr(event, "bot") and hasattr(event.bot, "delete_msg"):
                await event.bot.delete_msg(message_id=message_id)
                logger.info("[JM PDF plugin] 已自动撤回消息: %s", message_id)
                return

            if hasattr(event, "bot") and hasattr(event.bot, "recall_message"):
                try:
                    await event.bot.recall_message(int(message_id))
                    logger.info("[JM PDF plugin] 已自动撤回消息: %s", message_id)
                    return
                except (ValueError, TypeError):
                    logger.debug("[JM PDF plugin] recall_message 不支持 message_id=%s", message_id)

            if hasattr(event, "bot") and hasattr(event.bot, "api"):
                await event.bot.api.call_action("delete_msg", message_id=int(message_id))
                logger.info("[JM PDF plugin] 已自动撤回消息: %s", message_id)
                return

            logger.warning("[JM PDF plugin] 当前平台不支持撤回接口，message_id=%s", message_id)
        except Exception as e:
            logger.warning("[JM PDF plugin] 撤回消息失败: message_id=%s, err=%s", message_id, e)

        if enable_file_delete_fallback and fallback_file_name and self._is_group_chat(event):
            logger.info(
                "[JM PDF plugin] 尝试群文件删除兜底: message_id=%s, file=%s",
                message_id,
                fallback_file_name,
            )
            await self._delete_group_file_by_name(event, fallback_file_name)

    def schedule_recall(
        self,
        event: AstrMessageEvent,
        send_resp: Any,
        delay: int,
        scene: str = "",
        fallback_file_name: str | None = None,
        enable_file_delete_fallback: bool = False,
    ) -> bool:
        message_id = self._extract_message_id(send_resp)
        if not message_id:
            if enable_file_delete_fallback and fallback_file_name and self._is_group_chat(event):
                task = asyncio.create_task(
                    self._delete_group_file_after_delay(event, fallback_file_name, delay)
                )
                task.add_done_callback(self._remove_task)
                self._tasks.append(task)
                logger.warning(
                    "[JM PDF plugin] %s 未获取到 message_id，已切换群文件删除兜底: file=%s, delay=%s 秒",
                    scene or "消息",
                    fallback_file_name,
                    delay,
                )
                return True

            if enable_file_delete_fallback and fallback_file_name and not self._is_group_chat(event):
                logger.debug(
                    "[JM PDF plugin] %s 未获取到 message_id，且为私聊消息，跳过群文件删除兜底",
                    scene or "消息",
                )
                return False

            logger.warning(
                "[JM PDF plugin] %s 未获取到 message_id，无法撤回",
                scene or "消息",
            )
            return False

        task = asyncio.create_task(
            self._recall_msg(
                event,
                message_id,
                delay,
                fallback_file_name=fallback_file_name,
                enable_file_delete_fallback=enable_file_delete_fallback,
            )
        )
        task.add_done_callback(self._remove_task)
        self._tasks.append(task)
        logger.info(
            "[JM PDF plugin] 已创建撤回任务: scene=%s, message_id=%s, delay=%s 秒",
            scene or "default",
            message_id,
            delay,
        )
        return True

    async def send_plain(self, event: AstrMessageEvent, text: str):
        """按 help_typst 的策略发送纯文本并尽可能拿到 message_id。"""
        bot = getattr(event, "bot", None)
        payload = event.plain_result(text)

        # OneBot 优先：通过 call_action 发送，返回结构更稳定，便于提取 message_id
        if bot and hasattr(event, "_parse_onebot_json") and hasattr(bot, "call_action"):
            try:
                chain = payload.chain if hasattr(payload, "chain") else payload
                if not isinstance(chain, list):
                    chain = [chain]

                msg_chain = MessageChain(chain=chain)
                obmsg = await event._parse_onebot_json(msg_chain)

                params = {"message": obmsg}
                if gid := event.get_group_id():
                    params["group_id"] = int(gid)
                    action = "send_group_msg"
                elif uid := event.get_sender_id():
                    params["user_id"] = int(uid)
                    action = "send_private_msg"
                else:
                    raise ValueError("无法确定发送目标")

                resp = await bot.call_action(action, **params)
                message_id = self._extract_message_id(resp)
                logger.debug(
                    "[JM PDF plugin] send_plain(OneBot) 发送完成: action=%s, message_id=%s, resp_type=%s",
                    action,
                    message_id,
                    type(resp).__name__,
                )
                return message_id
            except Exception as e:
                logger.debug("[JM PDF plugin] OneBot 发送尝试失败，回退通用接口: %s", e)

        # 通用回退
        try:
            resp = await event.send(payload)
            message_id = self._extract_message_id(resp)
            logger.debug(
                "[JM PDF plugin] send_plain(fallback) 发送完成: message_id=%s, resp_type=%s",
                message_id,
                type(resp).__name__,
            )
            return message_id
        except Exception as e:
            logger.warning("[JM PDF plugin] 发送文本失败: %s", e)
            return None

    async def send_file(self, event: AstrMessageEvent, file_path: str, file_name: str):
        resp = await event.send(MessageChain([Comp.File(file=file_path, name=file_name)]))
        message_id = self._extract_message_id(resp)
        logger.debug(
            "[JM PDF plugin] send_file 发送完成: file=%s, message_id=%s, resp_type=%s",
            file_name,
            message_id,
            type(resp).__name__,
        )
        return resp
