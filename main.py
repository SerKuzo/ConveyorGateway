import logging
from logging.handlers import RotatingFileHandler
import sys
from config import settings
from services.file_watcher import ConveyorWatcher
import signal


def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S')

    # Консоль
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Файл (максимум 5 МБ, 3 архивных копии)
    file_handler = RotatingFileHandler('/opt/conveyor_gateway/conveyor.log', maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def main():
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("=== Запуск Conveyor Gateway ===")

    watcher = ConveyorWatcher(settings)

    # Обработка сигналов ОС (для systemd)
    def handle_signal(signum, frame):
        watcher.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    watcher.start()
    watcher.wait()  # Блокируем главный поток


if __name__ == "__main__":
    main()