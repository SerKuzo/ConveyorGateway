import time
import os
import shutil
import queue
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from parser import init_db, process_file

import logging
from datetime import datetime

WATCH_DIR = "/srv/conveyor_share"
FAILED_DIR = "/srv/conveyor_failed"
ARCHIVE_DIR = "/srv/conveyor_archive"

file_queue = queue.Queue()
queued_files = set()
queue_lock = threading.Lock()

# Настраиваем формат логов: [Дата Время] [Уровень] Сообщение
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# Создаем объект логгера для этого файла
logger = logging.getLogger(__name__)

class FileHandler(FileSystemEventHandler):
    def process_event(self, event):
        # Игнорируем папки
        if event.is_directory:
            return
        # Игнорируем временные файлы
        if event.src_path.endswith(('.tmp', '.swp', '.part')):
            return

        filename = os.path.basename(event.src_path)

        with queue_lock:
            if filename not in queued_files:
                queued_files.add(filename)
                file_queue.put(event.src_path)
                logger.info(f"[Watcher] Файл добавлен в очередь: {filename}")

    def enqueue_file(self, filepath):
        if not filepath or filepath.endswith(('.tmp', '.swp')):
            return

        filename = os.path.basename(filepath)
        with queue_lock:
            if filename not in queued_files:
                queued_files.add(filename)
                file_queue.put(filepath)
                logger.info(f"[Watcher] Файл добавлен в очередь: {filename}")

    def on_closed(self, event):
        # Срабатывает в Linux (inotify), когда процесс завершил запись и закрыл файл
        if not event.is_directory:
            self.enqueue_file(event.src_path)

    def on_moved(self, event):
        # Срабатывает при атомарном перемещении/переименовании файла в папку
        if not event.is_directory:
            self.enqueue_file(event.dest_path)


def worker():
    while True:
        filepath = file_queue.get()
        filename: str | bytes | LiteralString = os.path.basename(filepath)

        try:
            # Проверяем, существует ли файл и не пустой ли он
            if not os.path.exists(filepath):
                logger.info(f"[Worker] Файл {filename} уже отсутствует.")
                continue

            if os.path.getsize(filepath) == 0:
                logger.info(f"[Worker] Файл {filename} пуст (0 байт). Пропускаем.")
                continue

            # Сразу обрабатываем без искусственных задержек
            process_file(filepath)

            # Перемещаем в архив
            if os.path.exists(filepath):
                shutil.move(filepath, os.path.join(ARCHIVE_DIR, filename))
                logger.info(f"[Worker] Файл {filename} перемещен в архив.")

        except Exception as e:
            logger.error(f"[Worker] Ошибка при обработке {filename}: {e}")
            if os.path.exists(filepath):
                try:
                    shutil.move(filepath, os.path.join(FAILED_DIR, filename))
                except Exception:
                    pass
        finally:
            with queue_lock:
                queued_files.discard(filename)
            file_queue.task_done()


if __name__ == "__main__":
    os.makedirs(FAILED_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    init_db()

    for i in range(3):
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    event_handler = FileHandler()
    observer = Observer()
    observer.schedule(event_handler, WATCH_DIR, recursive=True)

    print(f"Запуск мониторинга папки: {WATCH_DIR}")
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
