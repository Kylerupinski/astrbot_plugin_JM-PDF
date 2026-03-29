'''
原项目地址：https://github.com/salikx/image2pdf/
作者：salikx
'''

import time
from pathlib import Path
from PIL import Image
from astrbot.api import logger

class Converter:
    '''PDF转化器'''
    def __init__(self, manga_id: str, base_dir: str = ""):
        '''
        Args:
            manga_id (str): 漫画ID
        '''
        self.manga_id = manga_id
        self.base_dir = base_dir

    def _get_base_dir(self) -> str:
        return self.base_dir
    
    def checkCache(self, chapter_id: int):
        '''检查是否存在缓存
        Args:
            chapter_id (int): 章节数
        Returns:
            True: 存在缓存
            False: 不存在缓存
        '''
        target_name = f"{self.manga_id}-{chapter_id}.pdf"
        path = Path(self._get_base_dir())
        
        pdf_path = path / target_name
        if pdf_path.exists():
            logger.info("[JM PDF plugin] PDF 缓存命中: %s", pdf_path)
            return True
        logger.debug("[JM PDF plugin] PDF 缓存未命中: %s", pdf_path)
        return False
    
    def manga2Pdf(self, chapter_id=1) -> tuple[int, str]:
        '''
        将目录下图片转换为pdf文件
        
        Args:
            chapter_id (int): 章节数
        '''
        save_folder = ""
        input_folder = ""
        save_folder = self._get_base_dir()
        input_folder = Path(save_folder) / self.manga_id

        logger.info(
            "[JM PDF plugin] 开始转换 PDF: manga_id=%s, chapter_id=%s, input_folder=%s, save_folder=%s",
            self.manga_id,
            chapter_id,
            input_folder,
            save_folder,
        )
        
        start_time = time.time()
        path = input_folder
        subdir = []
        image = []

        if self.checkCache(chapter_id):
            return 0, ""

        if not input_folder.exists():
            err = f"下载目录不存在: {input_folder}"
            logger.warning("[JM PDF plugin] %s", err)
            return -1, err

        if not input_folder.is_dir():
            err = f"下载路径不是目录: {input_folder}"
            logger.warning("[JM PDF plugin] %s", err)
            return -1, err
        
        for entry in path.iterdir():
            if entry.is_dir():
                try:
                    subdir.append(int(entry.name))
                except ValueError:
                    logger.debug("[JM PDF plugin] 跳过非数字章节目录: %s", entry)
        subdir.sort()
        logger.debug("[JM PDF plugin] 检测到章节目录: %s", subdir)
        subdir = [entry for entry in subdir if entry == int(chapter_id)]
        if subdir == []:
            err = f"jm{self.manga_id}的第{chapter_id}章不存在"
            logger.warning("[JM PDF plugin] %s", err)
            return -1, err
            
        for i in subdir:
            chapter_images = []
            chapter_dir = path / str(i)
            logger.debug("[JM PDF plugin] 开始扫描章节目录: %s", chapter_dir)
            for entry in chapter_dir.iterdir():
                if entry.is_dir():
                    err = f"{chapter_dir}目录下不应该有目录"
                    logger.warning("[JM PDF plugin] %s", err)
                    return -1, err
                if entry.is_file() and entry.suffix.lower() in ['.jpg', '.jpeg']:
                    chapter_images.append(str(entry))

            logger.info(
                "[JM PDF plugin] 章节扫描完成: manga_id=%s, chapter_id=%s, image_count=%s",
                self.manga_id,
                i,
                len(chapter_images),
            )
            
            try:    # 按文件名中的数字排序
                chapter_images.sort(key=lambda x: int(Path(x).stem))
            except (ValueError, IndexError):
                logger.warning("[JM PDF plugin] 文件名格式异常，使用字符串排序")
                chapter_images.sort()
                
            image.extend(chapter_images)
        
        if not image:
            err = "未找到任何图片文件"
            logger.warning(
                "[JM PDF plugin] %s: manga_id=%s, chapter_id=%s, input_folder=%s",
                err,
                self.manga_id,
                chapter_id,
                input_folder,
            )
            return -1, err

        # 将所有图片加载为独立的 RGB 对象，避免文件句柄长期占用。
        loaded_images: list[Image.Image] = []
        for file in image:
            if Path(file).suffix.lower() in ['.jpg', '.jpeg']:
                try:
                    with Image.open(file) as img_file:
                        if img_file.mode != "RGB":
                            normalized = img_file.convert("RGB")
                        else:
                            normalized = img_file.copy()
                    loaded_images.append(normalized)
                except Exception as e:
                    for loaded in loaded_images:
                        loaded.close()
                    err = f"处理图片 {file} 失败: {e}"
                    logger.warning("[JM PDF plugin] %s", err)
                    return -1, err

        if not loaded_images:
            err = "未找到有效的图片文件"
            logger.warning("[JM PDF plugin] %s", err)
            return -1, err

        output = loaded_images[0]
        sources = loaded_images[1:]

        pdf_file_path = Path(save_folder) / f"{self.manga_id}-{chapter_id}.pdf"
            
        '''保存PDF'''
        try:
            output.save(str(pdf_file_path), "PDF", save_all=True, append_images=sources)
            logger.info("[JM PDF plugin] PDF 保存成功: %s", pdf_file_path)
        except Exception as e:
            err = f"保存 PDF 失败: {e}"
            logger.warning("[JM PDF plugin] %s", err)
            return -1, err
        finally:
            for loaded in loaded_images:
                try:
                    loaded.close()
                except Exception:
                    pass
        end_time = time.time()
        run_time = end_time - start_time
        logger.info("[JM PDF plugin] PDF 转换耗时: %.2f 秒", run_time)
        return 0, ""
