from pathlib import Path

class FileHandler:
    '''处理文件操作'''
    def __init__(self, base_dir: str, manga_id: str, chapter_id: int):
        '''
        Args:
            base_dir (str): PDF 存储目录
        '''
        self.name = f"{manga_id}-{chapter_id}.pdf"
        self.file = str(Path(base_dir) / self.name)

    def get_file_path(self) -> str:
        return self.file