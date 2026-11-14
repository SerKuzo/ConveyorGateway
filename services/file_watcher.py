import os
import logging
import queue
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)

class FileHandler(FileSystemEventHandler):
    """
        Обработчик событий файловой системы.
        Отвечает только за то, чтобы поймать событие и положить путь в очередь.
        """

    def __init__(self, queue, queued_files, lock):
        super().__init__()
        self.queue = queue
        self.queued_files = queued_files
        self.lock = lock

    def process_event(self, event: object) -> None:
        # Игнорируем папки
        if event.is_directory:
            return
        # Игнорируем временные файлы
        if event.src_path.endswith(('.tmp', '.swp', '.part')):
            return

        filename = os.path.basename(event.src_path)
        logger.info(f"[Event] Поймано событие {event.event_type} для файла: {filename}")

        with self.lock:
            if filename not in  self.queued_files:
                self.queued_files.add(filename)
                self.queue.put(event.src_path)
                logger.info(f"[Watcher] Файл добавлен в очередь: {filename}")
            else:
                logger.info(f"[Watcher] Файл {filename} уже в очереди, пропускаем.")

    def on_closed(self, event):
        # Срабатывает в Linux (inotify), когда процесс завершил запись и закрыл файл
        if not event.is_directory:
            self.process_event(event)

    def on_moved(self, event):
        # Срабатывает при атомарном перемещении/переименовании файла в папку
        if not event.is_directory:
            self.process_event(event)

class ConveyorWatcher:
    def __init__(self, config):
        self.config = config
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.queued_files = set()
        self.lock = threading.Lock()
        self.workers = []

    def start(self):
        # Запускаем воркеров
        for i in range(self.config.WORKER_COUNT):
            t = threading.Thread(target=self._worker_loop, daemon=True, name=f"Worker-{i}")
            t.start()
            self.workers.append(t)
            logger.info(f"Запущен поток {t.name}")

        # Запускаем Observer
        event_handler = FileHandler(self.queue, self.queued_files, self.lock)
        self.observer = Observer()
        self.observer.schedule(event_handler, self.config.WATCH_DIR, recursive=True)
        self.observer.start()
        logger.info(f"Мониторинг директории: {self.config.WATCH_DIR}")

    def stop(self):
        logger.info("Получен сигнал остановки. Завершаем работу...")
        self.stop_event.set()
        self.observer.stop()
        self.observer.join()

        # Ждем, пока воркеры доедят очередь
        self.queue.join()
        logger.info("Все задачи завершены. Выход.")

    def _worker_loop(self):
        while not self.stop_event.is_set():
            try:
                filepath = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue

            filename = os.path.basename(filepath)
            try:
                self._process_file(filepath)
            except Exception as e:
                logger.error(f"[Worker] Ошибка при обработке {filename}: {e}")
            finally:
                # ОЧЕНЬ ВАЖНО: удаляем файл из сета, чтобы можно было обработать его снова
                with self.lock:
                    self.queued_files.discard(filename)
                self.queue.task_done()

    def wait(self):
        """Блокирует главный поток, пока не будет установлен stop_event."""
        # Ждем, пока событие остановки не будет установлено
        self.stop_event.wait()

    def _process_file(self, filepath):
        """
        Оркестрирует процесс обработки одного файла.
        Вызывается из воркеров.
        """
        import os
        from core.parser import calculate_hash, parse_counters, parse_recipe
        from core.database import db_manager

        filename = os.path.basename(filepath)

        # Определяем имя панели из пути (например, /srv/conveyor_share/LANGID_419/file.csv)
        panel_dir: str | bytes | LiteralString = os.path.dirname(filepath)
        panel_name = os.path.basename(panel_dir)
        if panel_name == "conveyor_share":  # Если файл лежит прямо в корне
            panel_name = "Unknown_Panel"

        # Считаем хеш файла
        file_hash = calculate_hash(filepath)

        # Определяем тип файла и обрабатываем
        try:
            if "CountData" in filename:
                if db_manager.check_file_processed(file_hash, panel_name, "counters"):
                    db_manager.update_heartbeat(file_hash, panel_name, "counters")
                    logger.info(f"[Heartbeat] Счетчики не изменились для {panel_name}: {filename}")
                else:
                    records = parse_counters(filepath)
                    if records:
                        db_manager.insert_counters(panel_name, filename, file_hash, records)
                        logger.info(f"[New Data] Добавлены счетчики для {panel_name}: {filename}")

            elif "record_" in filename:
                if db_manager.check_file_processed(file_hash, panel_name, "recipes"):
                    db_manager.update_heartbeat(file_hash, panel_name, "recipes")
                    logger.info(f"[Heartbeat] Рецепт не изменился для {panel_name}: {filename}")
                else:
                    recipe = parse_recipe(filepath)
                    if recipe:
                        db_manager.insert_recipe(panel_name, filename, file_hash, recipe)
                        logger.info(f"[New Data] Обновлен рецепт для {panel_name}: {filename}")

            elif "ALL_Рецепт" in filename:
                # Пока просто логируем, что файл найден (можно добавить логику позже)
                logger.info(f"[Info] Получен файл справочника рецептов: {filename}")

            else:
                logger.warning(f"[Warning] Неизвестный тип файла: {filename}")
                return  # Не перемещаем в архив, пусть лежит (или переместим в failed)

            # Если всё успешно, перемещаем файл в архив
            import shutil
            os.makedirs(self.config.ARCHIVE_DIR, exist_ok=True)
            shutil.move(filepath, os.path.join(self.config.ARCHIVE_DIR, filename))

        except Exception as e:
            logger.error(f"[Error] Ошибка при обработке {filename}: {e}")
            # Перемещаем битый файл в failed
            import shutil
            os.makedirs(self.config.FAILED_DIR, exist_ok=True)
            try:
                if os.path.exists(filepath):
                    shutil.move(filepath, os.path.join(self.config.FAILED_DIR, filename))
            except Exception as move_err:
                logger.error(f"Не удалось переместить файл в failed: {move_err}")
            raise e  # Пробрасываем ошибку, чтобы воркер знал о сбое