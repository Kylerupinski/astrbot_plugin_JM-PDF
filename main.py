from pathlib import Path
import asyncio
import ctypes
import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.filter.command import GreedyStr

from .handlers.jmclear import jmClear
from .handlers.jmmanga import jmManga
from .handlers.jmrank import jmRank
from .handlers.jmsearch import jmSearch
from .utils.cachecleaner import CacheCleaner
from .utils.msg_recall import MsgRecall


@register(
    "astrbot_plugin_jm_pdf",
    "Kylerupinski",
    "JM 漫画下载并转换 PDF",
    "1.6.4",
    "https://github.com/Kylerupinski/astrbot_plugin_JM_PDF",
)
class JMPDFPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        
        # 数据目录（将在 initialize 中初始化）
        self._data_dir: Path = Path()
        self.download_base_dir = ""
        self.jm_config = {}
        self._cache_clean_job_id: str = ""
        self._cache_clean_lock = asyncio.Lock()
        self._active_manga_lock = asyncio.Lock()
        self._active_manga_refcount: dict[str, int] = {}
        self._request_state_lock = asyncio.Lock()
        self._request_state: dict[str, dict[str, float | bool]] = {}
        self.msg_recall = MsgRecall()
        self.max_concurrent_downloads = max(
            1,
            int(
                self._cfg_value(
                    self._runtime_cfg,
                    "max_concurrent_downloads",
                    1,
                )
            ),
        )
        self.max_download_batch_count = max(
            1,
            int(
                self._cfg_value(
                    self._runtime_cfg,
                    "max_download_batch_count",
                    8,
                )
            ),
        )
        self.low_memory_protection = bool(
            self._cfg_value(
                self._runtime_cfg,
                "low_memory_protection",
                True,
            )
        )
        self.low_memory_floor_mb = max(
            128,
            int(
                self._cfg_value(
                    self._runtime_cfg,
                    "low_memory_floor_mb",
                    900,
                )
            ),
        )
        self._download_semaphore = asyncio.Semaphore(self.max_concurrent_downloads)

        self.maxfilecount = int(
            self._cfg_value(self._runtime_cfg, "max_cache_count", 50)
        )
        self.recall_seconds = int(
            self._cfg_value(self._runtime_cfg, "recall_seconds", 60)
        )
        self.recall_get_notice = bool(
            self._cfg_value(self._runtime_cfg, "recall_get_notice", False)
        )
        self.recall_get_file = bool(
            self._cfg_value(self._runtime_cfg, "recall_get_file", True)
        )
        self.recall_search_messages = bool(
            self._cfg_value(self._runtime_cfg, "recall_search_messages", False)
        )
        self.recall_rank_messages = bool(
            self._cfg_value(self._runtime_cfg, "recall_rank_messages", False)
        )
        self.download_timeout_seconds = int(
            self._cfg_value(self._runtime_cfg, "download_timeout_seconds", 180)
        )
        self.request_debounce_seconds = max(
            0,
            int(
                self._cfg_value(
                    self._runtime_cfg,
                    "request_debounce_seconds",
                    8,
                )
            ),
        )
        self.query_timeout_seconds = int(
            self._cfg_value(self._runtime_cfg, "query_timeout_seconds", 10)
        )
        self.enable_auto_clean_cache = bool(
            self._cfg_value(self._runtime_cfg, "enable_auto_clean_cache", False)
        )
        self.fallback_send_path = bool(
            self._cfg_value(
                self._runtime_cfg,
                "fallback_send_path",
                False,
            )
        )
        self.enable_text_match = bool(
            self._cfg_value(
                self._command_cfg,
                "enable_text_match",
                False,
            )
        )
        self.whitelist = set(
            self._cfg_value(self._runtime_cfg, "whitelist", [])
        )

        self.command_enabled = {
            "download": bool(
                self._cfg_value(
                    self._command_cfg,
                    "enable_download",
                    True,
                )
            ),
            "search": bool(
                self._cfg_value(
                    self._command_cfg,
                    "enable_search",
                    True,
                )
            ),
            "clear": bool(
                self._cfg_value(
                    self._command_cfg,
                    "enable_clear",
                    True,
                )
            ),
            "rank": bool(
                self._cfg_value(
                    self._command_cfg,
                    "enable_rank",
                    True,
                )
            ),
            "text": self.enable_text_match,
        }

    @property
    def _jm_cfg(self) -> dict:
        return self.config.get("jm_config", {})

    @property
    def _runtime_cfg(self) -> dict:
        return self.config.get("runtime_config", {})

    @property
    def _command_cfg(self) -> dict:
        return self.config.get("command_config", {})

    def _cfg_value(self, section: dict, key: str, default):
        """从配置 section 中获取值，如果不存在则使用默认值。"""
        return section.get(key, default)

    def _request_user_key(self, event: AstrMessageEvent) -> str:
        """生成用户级防抖 key，跨 get/search/rank 共享。"""
        platform = "unknown"
        if hasattr(event, "get_platform_name"):
            try:
                platform = str(event.get_platform_name() or "unknown")
            except Exception:
                platform = "unknown"

        origin = str(getattr(event, "unified_msg_origin", "") or "")

        sender = ""
        if hasattr(event, "get_sender_id"):
            try:
                sender = str(event.get_sender_id() or "")
            except Exception:
                sender = ""

        if sender:
            return f"{platform}:{origin}:{sender}"
        return f"{platform}:{origin}"

    async def _try_acquire_user_request_slot(self, event: AstrMessageEvent) -> bool:
        """尝试占用用户请求槽位：执行中或防抖窗口内返回 False。"""
        key = self._request_user_key(event)
        now = time.monotonic()

        async with self._request_state_lock:
            state = self._request_state.get(key)
            if state:
                in_progress = bool(state.get("in_progress", False))
                debounce_until = float(state.get("debounce_until", 0.0) or 0.0)
                if in_progress or now < debounce_until:
                    return False

            self._request_state[key] = {
                "in_progress": True,
                "debounce_until": 0.0,
            }
            return True

    async def _release_user_request_slot(self, event: AstrMessageEvent):
        """释放用户请求槽位，并进入防抖窗口。"""
        key = self._request_user_key(event)
        now = time.monotonic()

        async with self._request_state_lock:
            if self.request_debounce_seconds <= 0:
                self._request_state.pop(key, None)
                return

            state = self._request_state.get(key, {})
            state["in_progress"] = False
            state["debounce_until"] = now + self.request_debounce_seconds
            self._request_state[key] = state

    def _get_available_memory_mb(self) -> int | None:
        """获取当前可用内存(MB)。失败时返回 None。"""
        try:
            mem_status = ctypes.c_ulonglong()

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            statex = MEMORYSTATUSEX()
            statex.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(statex)):
                mem_status = statex.ullAvailPhys
                return max(0, int(mem_status // (1024 * 1024)))
        except Exception:
            pass

        try:
            meminfo_path = Path("/proc/meminfo")
            if meminfo_path.exists():
                for line in meminfo_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if line.startswith("MemAvailable:"):
                        kb = int(line.split()[1])
                        return max(0, kb // 1024)
        except Exception:
            pass

        return None

    def _resolve_effective_batch_count(self) -> int:
        configured_batch = max(
            1,
            int(
                self._cfg_value(
                    self._jm_cfg,
                    "batch_count",
                    45,
                )
            ),
        )
        base_batch = min(configured_batch, self.max_download_batch_count)

        if not self.low_memory_protection:
            return base_batch

        avail_mb = self._get_available_memory_mb()
        if avail_mb is None:
            return base_batch

        # 低内存场景分级降并发，优先保证宿主机稳定。
        if avail_mb <= self.low_memory_floor_mb:
            return 1
        if avail_mb <= self.low_memory_floor_mb + 300:
            return min(base_batch, 2)
        if avail_mb <= self.low_memory_floor_mb + 600:
            return min(base_batch, 3)
        if avail_mb <= self.low_memory_floor_mb + 900:
            return min(base_batch, 4)
        return base_batch

    def _build_jm_config_dict(self, download_base_dir: Path) -> dict:
        """根据 _conf_schema 配置构建 jmcomic 配置字典。"""
        api_domains = self._cfg_value(
            self._jm_cfg,
            "client_api_domains",
            [],
        )

        effective_batch_count = self._resolve_effective_batch_count()

        cfg = {
            "version": "2.0",
            "dir_rule": {
                "base_dir": str(download_base_dir),
                "rule": self._cfg_value(
                    self._jm_cfg,
                    "dir_rule",
                    "Bd_Aid_Pindex",
                ),
            },
            "client": {
                "impl": self._cfg_value(
                    self._jm_cfg,
                    "client_impl",
                    "api",
                ),
                "domain": {
                    "api": api_domains,
                },
            },
            "download": {
                "cache": bool(
                    self._cfg_value(
                        self._jm_cfg,
                        "download_cache",
                        True,
                    )
                ),
                "image": {
                    "decode": bool(
                        self._cfg_value(
                            self._jm_cfg,
                            "image_decode",
                            True,
                        )
                    ),
                    "suffix": self._cfg_value(
                        self._jm_cfg,
                        "image_suffix",
                        ".jpg",
                    ),
                },
                "threading": {
                    "batch_count": effective_batch_count,
                },
            },
        }

        logger.info(
            "[JM PDF plugin] 下载线程参数已应用: configured=%s, cap=%s, effective=%s, low_memory_protection=%s",
            self._cfg_value(self._jm_cfg, "batch_count", 45),
            self.max_download_batch_count,
            effective_batch_count,
            self.low_memory_protection,
        )

        username = str(
            self._cfg_value(
                self._jm_cfg,
                "login_username",
                "",
            )
        ).strip()
        password = str(
            self._cfg_value(
                self._jm_cfg,
                "login_password",
                "",
            )
        ).strip()
        if username and password:
            cfg["plugins"] = {
                "after_init": [
                    {
                        "plugin": "login",
                        "kwargs": {
                            "username": username,
                            "password": password,
                        },
                    }
                ]
            }
        return cfg

    async def initialize(self):
        """插件初始化：初始化数据目录、生成 JM 配置。"""
        # 初始化数据目录（参考 arxiv 插件设计）
        self._data_dir = StarTools.get_data_dir("astrbot_plugin_jm_pdf")
        
        # 创建下载目录（下载内容统一视为缓存：章节目录、jpg、pdf）
        download_dir = self._data_dir / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        self.download_base_dir = str(download_dir)
        
        # 生成 JM 配置字典
        self.jm_config = self._build_jm_config_dict(download_dir)

        await self._register_auto_clean_job()
        
        logger.info(
            "[JM PDF plugin] 已初始化。数据目录: %s, 下载缓存目录: %s",
            self._data_dir,
            download_dir,
        )

    async def terminate(self):
        """插件卸载时清理。"""
        if self._cache_clean_job_id:
            try:
                await self.context.cron_manager.delete_job(self._cache_clean_job_id)
                logger.info("[JM PDF plugin] 自动清理定时任务已卸载")
            except Exception:
                logger.exception("[JM PDF plugin] 卸载自动清理定时任务失败")

        try:
            await self.msg_recall.terminate()
            logger.info("[JM PDF plugin] 撤回任务已清理")
        except Exception:
            logger.exception("[JM PDF plugin] 清理撤回任务失败")
        logger.info("[JM PDF plugin] 插件已卸载。数据保存在: %s", self._data_dir)

    async def _register_auto_clean_job(self):
        """注册每日 4:00 自动清理缓存任务。"""
        if not self.enable_auto_clean_cache:
            logger.info("[JM PDF plugin] 自动清理缓存已关闭")
            return

        try:
            job = await self.context.cron_manager.add_basic_job(
                name="jm_pdf_auto_clean_cache",
                cron_expression="0 4 * * *",
                handler=self._scheduled_clean_cache,
                description="JM PDF 每日自动清理缓存",
                timezone="Asia/Shanghai",
                enabled=True,
                persistent=False,
            )
            self._cache_clean_job_id = job.job_id
            logger.info("[JM PDF plugin] 已注册自动清理任务: 每日 04:00")
        except Exception:
            logger.exception("[JM PDF plugin] 注册自动清理任务失败")

    async def _scheduled_clean_cache(self):
        """定时任务：自动清理缓存。"""
        logger.info("[JM PDF plugin] 自动清理任务开始执行")
        try:
            summary = await self._trigger_cache_clean()
            logger.info("[JM PDF plugin] 自动清理任务执行完成: summary=%s", summary)
        except Exception:
            logger.exception("[JM PDF plugin] 自动清理任务执行失败")

    def _in_whitelist(self, event: AstrMessageEvent) -> bool:
        if not self.whitelist:
            return True
        return event.unified_msg_origin in self.whitelist

    async def _mark_active_manga(self, manga_id: str, active: bool):
        """记录进行中的 manga_id，供缓存清理时跳过。"""
        async with self._active_manga_lock:
            if active:
                self._active_manga_refcount[manga_id] = self._active_manga_refcount.get(manga_id, 0) + 1
            else:
                current = self._active_manga_refcount.get(manga_id, 0)
                if current <= 1:
                    self._active_manga_refcount.pop(manga_id, None)
                else:
                    self._active_manga_refcount[manga_id] = current - 1

    async def _get_active_manga_ids(self) -> set[str]:
        async with self._active_manga_lock:
            return set(self._active_manga_refcount.keys())

    def _clean_cache(self, force: bool = False, protected_manga_ids: set[str] | None = None):
        cleaner = CacheCleaner(self.download_base_dir, self.maxfilecount)
        return cleaner.cleanCache(force=force, protected_manga_ids=protected_manga_ids)

    async def _trigger_cache_clean(self, force: bool = False):
        protected_manga_ids = await self._get_active_manga_ids()
        async with self._cache_clean_lock:
            return await asyncio.to_thread(
                self._clean_cache,
                force,
                protected_manga_ids,
            )

    async def _do_download(self, event: AstrMessageEvent, manga_id: str, chapter: int | None):
        chapter_arg = None if chapter is None else str(chapter)
        async with self._download_semaphore:
            await self._mark_active_manga(manga_id, True)
            try:
                current_jm_config = self._build_jm_config_dict(Path(self.download_base_dir))
                async for result in jmManga(
                    event=event,
                    args=[manga_id, chapter_arg],
                    jm_config=current_jm_config,
                    download_base_dir=self.download_base_dir,
                    fallback_send_path=self.fallback_send_path,
                    recall_seconds=self.recall_seconds,
                    recall_get_notice=self.recall_get_notice,
                    recall_get_file=self.recall_get_file,
                    download_timeout_seconds=self.download_timeout_seconds,
                    msg_recall=self.msg_recall,
                ):
                    yield result
            finally:
                await self._mark_active_manga(manga_id, False)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """可选自动识别：消息出现 6-7 位数字时自动当作 jm id。"""
        msg = (event.message_str or "").strip()
        if not msg or not self.command_enabled["text"]:
            return
        if not self.command_enabled["download"]:
            return
        if not self._in_whitelist(event):
            return
        if msg.startswith("/"):
            return

        manga_id = "".join(char for char in msg if char.isdigit())
        if 6 <= len(manga_id) <= 7:
            acquired = await self._try_acquire_user_request_slot(event)
            if not acquired:
                yield event.plain_result("上一份请求还未完成，请耐心等待...")
                return

            try:
                yield event.plain_result(f"检测到 jm 号 {manga_id}")
                await self._trigger_cache_clean()
                async for result in self._do_download(event, manga_id, None):
                    yield result
            finally:
                await self._release_user_request_slot(event)

    @filter.command_group("jm")
    def jm_group(self):
        """JM 漫画相关指令组。"""

    @jm_group.command("get")
    async def cmd_get(self, event: AstrMessageEvent, manga_id: str = "", chapter: int = 1):
        """下载指定 JM 漫画章节并转换为 PDF。"""
        if not self._in_whitelist(event):
            return
        if not self.command_enabled["download"]:
            yield event.plain_result("该命令已禁用")
            return
        manga_id = manga_id.strip()
        if not manga_id or not manga_id.isdigit():
            yield event.plain_result("参数错误。用法: /jm get <manga_id> [chapter]")
            return
        if chapter <= 0:
            yield event.plain_result("chapter 必须大于 0")
            return

        acquired = await self._try_acquire_user_request_slot(event)
        if not acquired:
            yield event.plain_result("上一份请求还未完成，请耐心等待...")
            return

        try:
            await self._trigger_cache_clean()
            async for result in self._do_download(event, manga_id, chapter):
                yield result
        finally:
            await self._release_user_request_slot(event)

    @jm_group.command("search")
    async def cmd_search(self, event: AstrMessageEvent, query: GreedyStr = GreedyStr("")):
        """搜索 JM 漫画。"""
        if not self._in_whitelist(event):
            return
        if not self.command_enabled["search"]:
            yield event.plain_result("该命令已禁用")
            return

        query = query.strip()
        if not query:
            yield event.plain_result("参数错误。用法: /jm search <关键词>")
            return

        acquired = await self._try_acquire_user_request_slot(event)
        if not acquired:
            yield event.plain_result("上一份请求还未完成，请耐心等待...")
            return

        try:
            await self._trigger_cache_clean()
            async for result in jmSearch(
                event=event,
                args=[query],
                jm_config=self.jm_config,
                recall_seconds=self.recall_seconds,
                recall_search_messages=self.recall_search_messages,
                query_timeout_seconds=self.query_timeout_seconds,
                msg_recall=self.msg_recall,
            ):
                yield result
        finally:
            await self._release_user_request_slot(event)

    @jm_group.command("rank")
    async def cmd_rank(self, event: AstrMessageEvent, duration: str = "week"):
        """查询 JM 排行榜。"""
        if not self._in_whitelist(event):
            return
        if not self.command_enabled["rank"]:
            yield event.plain_result("该命令已禁用")
            return

        duration = duration.strip().lower()
        if duration not in {"week", "month"}:
            yield event.plain_result("参数错误。用法: /jm rank [week|month]")
            return

        acquired = await self._try_acquire_user_request_slot(event)
        if not acquired:
            yield event.plain_result("上一份请求还未完成，请耐心等待...")
            return

        try:
            await self._trigger_cache_clean()
            async for result in jmRank(
                event=event,
                args=[duration],
                jm_config=self.jm_config,
                recall_seconds=self.recall_seconds,
                recall_rank_messages=self.recall_rank_messages,
                query_timeout_seconds=self.query_timeout_seconds,
                msg_recall=self.msg_recall,
            ):
                yield result
        finally:
            await self._release_user_request_slot(event)

    @jm_group.command("clear")
    async def cmd_clear(self, event: AstrMessageEvent):
        """清理下载缓存。"""
        if not self._in_whitelist(event):
            return
        if not self.command_enabled["clear"]:
            yield event.plain_result("该命令已禁用")
            return

        protected_manga_ids = await self._get_active_manga_ids()
        async for result in jmClear(
            event=event,
            args=[],
            download_base_dir=self.download_base_dir,
            max_cache_count=self.maxfilecount,
            protected_manga_ids=protected_manga_ids,
        ):
            yield result

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        """调试日志：记录发送后的 message_id，便于排查撤回/文件发送问题。"""
        try:
            message_obj = getattr(event, "message_obj", None)
            message_id = getattr(message_obj, "message_id", None)
            platform = event.get_platform_name() if hasattr(event, "get_platform_name") else "unknown"

            result = event.get_result() if hasattr(event, "get_result") else None
            preview = "<non-plain>"
            if result and hasattr(result, "get_plain_text"):
                plain_text = result.get_plain_text() or ""
                if plain_text:
                    preview = plain_text[:80]

            logger.debug(
                "[JM PDF plugin] after_message_sent: platform=%s, message_id=%s, preview=%s",
                platform,
                message_id,
                preview,
            )
        except Exception as e:
            logger.debug("[JM PDF plugin] after_message_sent 调试日志失败: %s", e)

