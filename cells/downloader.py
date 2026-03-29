from pathlib import Path
import multiprocessing as mp
import queue

import jmcomic
from astrbot.api import logger


def _download_worker(result_queue: mp.Queue, manga_id: str, jm_config: dict, base_dir: str):
    """子进程下载入口：执行下载并将结果写入队列。"""
    try:
        downloader = Downloader(manga_id, jm_config=jm_config, base_dir=base_dir)
        result_queue.put(downloader.downloadManga())
    except Exception as e:
        result_queue.put((-1, f"下载子进程异常: {e}"))


def _download_worker_chapter(
    result_queue: mp.Queue,
    manga_id: str,
    chapter_id: int,
    jm_config: dict,
    base_dir: str,
):
    """子进程下载入口：只下载指定章节并将结果写入队列。"""
    try:
        downloader = Downloader(manga_id, jm_config=jm_config, base_dir=base_dir)
        result_queue.put(downloader.downloadManga(chapter_id=chapter_id))
    except Exception as e:
        result_queue.put((-1, f"下载子进程异常: {e}"))


def run_download_with_hard_timeout(
    manga_id: str,
    jm_config: dict,
    base_dir: str,
    timeout_seconds: int,
) -> tuple[int, str, bool]:
    """
    使用子进程执行下载，超时时强制终止。

    Returns:
        (status, err, timed_out)
    """
    timeout = max(1, int(timeout_seconds))
    ctx = mp.get_context("spawn")
    result_queue: mp.Queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(
        target=_download_worker,
        args=(result_queue, manga_id, jm_config, base_dir),
        daemon=True,
    )

    logger.info(
        "[JM PDF plugin] 启动下载子进程: manga_id=%s, timeout=%s 秒",
        manga_id,
        timeout,
    )

    proc.start()
    proc.join(timeout=timeout)

    if proc.is_alive():
        logger.warning(
            "[JM PDF plugin] 下载超时，终止子进程: manga_id=%s, pid=%s, timeout=%s 秒",
            manga_id,
            proc.pid,
            timeout,
        )
        proc.terminate()
        proc.join(timeout=3)
        if proc.is_alive():
            logger.warning(
                "[JM PDF plugin] 子进程 terminate 后仍存活，执行 kill: manga_id=%s, pid=%s",
                manga_id,
                proc.pid,
            )
            try:
                proc.kill()
            except Exception:
                pass
            proc.join(timeout=3)

        try:
            result_queue.close()
        except Exception:
            pass
        return -1, "下载超时，任务已强制中止", True

    try:
        status, err = result_queue.get_nowait()
    except queue.Empty:
        status, err = -1, "下载子进程未返回结果"
    except Exception as e:
        status, err = -1, f"读取下载结果失败: {e}"

    try:
        result_queue.close()
    except Exception:
        pass

    return int(status), str(err), False


def run_download_chapter_with_hard_timeout(
    manga_id: str,
    chapter_id: int,
    jm_config: dict,
    base_dir: str,
    timeout_seconds: int,
) -> tuple[int, str, bool]:
    """
    使用子进程执行指定章节下载，超时时强制终止。

    Returns:
        (status, err, timed_out)
    """
    timeout = max(1, int(timeout_seconds))
    ctx = mp.get_context("spawn")
    result_queue: mp.Queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(
        target=_download_worker_chapter,
        args=(result_queue, manga_id, int(chapter_id), jm_config, base_dir),
        daemon=True,
    )

    logger.info(
        "[JM PDF plugin] 启动章节下载子进程: manga_id=%s, chapter_id=%s, timeout=%s 秒",
        manga_id,
        chapter_id,
        timeout,
    )

    proc.start()
    proc.join(timeout=timeout)

    if proc.is_alive():
        logger.warning(
            "[JM PDF plugin] 章节下载超时，终止子进程: manga_id=%s, chapter_id=%s, pid=%s, timeout=%s 秒",
            manga_id,
            chapter_id,
            proc.pid,
            timeout,
        )
        proc.terminate()
        proc.join(timeout=3)
        if proc.is_alive():
            logger.warning(
                "[JM PDF plugin] 子进程 terminate 后仍存活，执行 kill: manga_id=%s, chapter_id=%s, pid=%s",
                manga_id,
                chapter_id,
                proc.pid,
            )
            try:
                proc.kill()
            except Exception:
                pass
            proc.join(timeout=3)

        try:
            result_queue.close()
        except Exception:
            pass
        return -1, "下载超时，任务已强制中止", True

    try:
        status, err = result_queue.get_nowait()
    except queue.Empty:
        status, err = -1, "下载子进程未返回结果"
    except Exception as e:
        status, err = -1, f"读取下载结果失败: {e}"

    try:
        result_queue.close()
    except Exception:
        pass

    return int(status), str(err), False

class Downloader:
    '''漫画下载器'''
    def __init__(self, manga_id: str, jm_config: dict, base_dir: str = ""):
        '''
        Args:
            manga_id (str): 漫画id
        '''
        self.manga_id = manga_id
        self.jm_config = jm_config or {}
        self.base_dir = base_dir

    def _build_jm_option(self):
        cfg = dict(self.jm_config)
        if self.base_dir:
            cfg.setdefault("dir_rule", {})["base_dir"] = self.base_dir
        logger.debug(
            "[JM PDF plugin] 构建 jm 配置: manga_id=%s, base_dir=%s, dir_rule=%s, client_impl=%s",
            self.manga_id,
            cfg.get("dir_rule", {}).get("base_dir"),
            cfg.get("dir_rule", {}).get("rule"),
            cfg.get("client", {}).get("impl"),
        )
        return jmcomic.JmOption.construct(cfg, cover_default=True)

    def _snapshot_manga_dir(self) -> tuple[int, int]:
        """返回(章节目录数, 图片文件数)，用于诊断下载状态。"""
        path = self.base_dir or self.jm_config.get("dir_rule", {}).get("base_dir", "")
        manga_path = Path(path) / self.manga_id
        if not manga_path.exists() or not manga_path.is_dir():
            return 0, 0

        chapter_dirs = sum(1 for p in manga_path.iterdir() if p.is_dir())
        image_count = 0
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            image_count += len(list(manga_path.rglob(ext)))
        return chapter_dirs, image_count

    def _snapshot_chapter_dir(self, chapter_id: int) -> int:
        """返回指定章节目录中的图片数量。"""
        path = self.base_dir or self.jm_config.get("dir_rule", {}).get("base_dir", "")
        chapter_path = Path(path) / self.manga_id / str(chapter_id)
        if not chapter_path.exists() or not chapter_path.is_dir():
            return 0

        image_count = 0
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            image_count += len(list(chapter_path.glob(ext)))
        return image_count

    def checkCache(self, chapter_id: int | None = None) -> bool:
        '''
        检查是否缓存漫画
        '''
        path = self.base_dir or self.jm_config.get("dir_rule", {}).get("base_dir", "")
        manga_path = Path(path) / self.manga_id

        if not path:
            logger.warning("[JM PDF plugin] 未配置下载目录，无法检查缓存: manga_id=%s", self.manga_id)
            return False

        if not manga_path.exists():
            logger.debug("[JM PDF plugin] 未命中缓存目录: %s", manga_path)
            return False

        if chapter_id is not None:
            chapter_path = manga_path / str(chapter_id)
            if not chapter_path.exists() or not chapter_path.is_dir():
                logger.debug("[JM PDF plugin] 指定章节缓存未命中: %s", chapter_path)
                return False

            image_count = self._snapshot_chapter_dir(chapter_id)
            if image_count > 0:
                logger.info(
                    "[JM PDF plugin] 命中章节缓存: manga_id=%s, chapter_id=%s, path=%s, images=%s",
                    self.manga_id,
                    chapter_id,
                    chapter_path,
                    image_count,
                )
                return True

            logger.warning(
                "[JM PDF plugin] 检测到空章节目录，视为未命中并重新下载: manga_id=%s, chapter_id=%s, path=%s",
                self.manga_id,
                chapter_id,
                chapter_path,
            )
            return False

        # 只有在目录中存在至少一张图片时，才认为缓存命中，避免空目录误判。
        image_count = 0
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            image_count += len(list(manga_path.rglob(ext)))
            if image_count > 0:
                break

        if image_count > 0:
            logger.info(
                "[JM PDF plugin] 命中缓存: manga_id=%s, path=%s, images=%s",
                self.manga_id,
                manga_path,
                image_count,
            )
            return True

        logger.warning(
            "[JM PDF plugin] 检测到空缓存目录，视为未命中并重新下载: manga_id=%s, path=%s",
            self.manga_id,
            manga_path,
        )
        return False

    def _resolve_target_photo_id(self, client, chapter_id: int) -> tuple[str | None, str]:
        """根据章节序号解析目标 photo_id。"""
        try:
            album = client.get_album_detail(self.manga_id)
        except Exception as e:
            return None, f"获取专辑详情失败: {e}"

        candidates = []

        episode_list = getattr(album, "episode_list", None)
        if episode_list:
            for idx, ep in enumerate(episode_list, start=1):
                photo_id = None
                if isinstance(ep, (tuple, list)) and ep:
                    photo_id = ep[0]
                else:
                    photo_id = getattr(ep, "photo_id", None) or getattr(ep, "id", None)
                if photo_id:
                    candidates.append((idx, str(photo_id)))

        if not candidates:
            photos = getattr(album, "photos", None) or getattr(album, "photo_list", None)
            if photos:
                for idx, p in enumerate(photos, start=1):
                    photo_id = getattr(p, "photo_id", None) or getattr(p, "id", None)
                    if photo_id:
                        candidates.append((idx, str(photo_id)))

        if not candidates:
            return None, "无法解析章节列表，请检查 jmcomic 版本或目录规则"

        if chapter_id > len(candidates):
            return None, f"jm{self.manga_id} 的第 {chapter_id} 章不存在（共 {len(candidates)} 章）"

        return candidates[chapter_id - 1][1], ""

    def downloadManga(self, chapter_id: int | None = None) -> tuple[int, str]:
        '''
        下载漫画
        '''
        logger.info("[JM PDF plugin] 开始下载漫画: manga_id=%s, chapter_id=%s", self.manga_id, chapter_id)
        loadConfig = self._build_jm_option()
        if self.checkCache(chapter_id):
            logger.info("[JM PDF plugin] 使用缓存跳过下载: manga_id=%s, chapter_id=%s", self.manga_id, chapter_id)
            return 0, ""

        before_chapters, before_images = self._snapshot_manga_dir()
        logger.debug(
            "[JM PDF plugin] 下载前目录状态: manga_id=%s, chapter_dirs=%s, images=%s",
            self.manga_id,
            before_chapters,
            before_images,
        )

        try:
            if chapter_id is None:
                jmcomic.download_album(self.manga_id, loadConfig)
            else:
                client = None
                try:
                    client = loadConfig.new_jm_client()
                except Exception:
                    client_cfg = self.jm_config.get("client", {}) if self.jm_config else {}
                    domain_list = client_cfg.get("domain", {}).get("api", None)
                    impl = client_cfg.get("impl", "api")
                    client = jmcomic.JmOption.default().new_jm_client(
                        domain_list=domain_list,
                        impl=impl,
                    )
                target_photo_id, resolve_err = self._resolve_target_photo_id(client, int(chapter_id))
                if not target_photo_id:
                    return -1, resolve_err

                logger.info(
                    "[JM PDF plugin] 解析章节成功: manga_id=%s, chapter_id=%s, photo_id=%s",
                    self.manga_id,
                    chapter_id,
                    target_photo_id,
                )
                jmcomic.download_photo(target_photo_id, loadConfig)

            after_chapters, after_images = self._snapshot_manga_dir()
            logger.info(
                "[JM PDF plugin] 漫画下载完成: manga_id=%s, chapter_dirs=%s, images=%s",
                self.manga_id,
                after_chapters,
                after_images,
            )

            if chapter_id is not None:
                chapter_images = self._snapshot_chapter_dir(int(chapter_id))
                if chapter_images == 0:
                    logger.warning(
                        "[JM PDF plugin] 指定章节下载完成但未发现章节图片: manga_id=%s, chapter_id=%s",
                        self.manga_id,
                        chapter_id,
                    )
                    return -1, f"章节 {chapter_id} 下载结果为空，请稍后重试"

            if after_images == 0:
                logger.warning(
                    "[JM PDF plugin] 下载调用返回成功，但未发现图片文件: manga_id=%s, chapter_dirs=%s",
                    self.manga_id,
                    after_chapters,
                )
            return 0, ""
        except jmcomic.MissingAlbumPhotoException as e:
            err = f"id={e.error_jmid}的本子不存在或需要配置登录信息"
            logger.warning("[JM PDF plugin] 下载失败(专辑不存在或需登录): %s", err)
            return 1, err
        except jmcomic.JmcomicException as e:
            err = f'jmcomic遇到异常: {e}'
            logger.warning("[JM PDF plugin] 下载失败(jmcomic异常): %s", err)
            return -1, err
        except Exception as e:
            err = f"下载失败(未知异常): {e}"
            logger.warning("[JM PDF plugin] %s", err)
            return -1, err