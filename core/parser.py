import csv
import hashlib
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)


# Определяем структуру данных (DTO)
@dataclass
class CounterRecord:
    var_name: str
    time_string: Optional[datetime]
    var_value: float
    validity: int
    time_ms: str


def calculate_hash(filepath: str) -> str:
    """Вычисляет SHA-256 хеш файла для проверки на дубликаты."""
    hash_sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


def parse_counters(filepath: str, dec_symbol: str = ',') -> List[CounterRecord]:
    """
    Читает файл CountData и возвращает список объектов CounterRecord.
    :param filepath: Путь к файлу.
    :param dec_symbol: Десятичный разделитель (по умолчанию запятая).
    """
    records = []

    try:
        # Используем кодировку cp1251, так как WinCC часто выдает ее
        with open(filepath, 'r', encoding='cp1251') as f:
            # Определяем разделитель списка (обычно ';')
            # В файлах CountData0.csv нет первой строки с настройками, поэтому берем ';' по умолчанию
            list_sep = ';'

            reader = csv.DictReader(f, delimiter=list_sep)

            # WinCC иногда ставит кавычки в заголовках, убираем их
            if reader.fieldnames:
                reader.fieldnames = [name.strip('"') for name in reader.fieldnames]

            for row in reader:
                # Пропускаем пустые строки или строки без имени переменной
                if not row.get('VarName'):
                    continue

                # Заменяем десятичный разделитель на точку для корректного преобразования в float
                value_str = row.get('VarValue', '0').replace(dec_symbol, '.')
                time_ms = row.get('Time_ms', '').replace(dec_symbol, '.')

                # Парсинг времени (формат WinCC: 17.08.2026 9:09:50)
                time_str = row.get('TimeString')
                dt = None
                if time_str:
                    try:
                        dt = datetime.strptime(time_str, "%d.%m.%Y %H:%M:%S")
                    except ValueError as e:
                        logger.warning(f"Не удалось распарсить время '{time_str}' в файле {filepath}: {e}")

                try:
                    # Создаем типизированный объект
                    record = CounterRecord(
                        var_name=row.get('VarName', '').strip('"'),
                        time_string=dt,
                        var_value=float(value_str),
                        validity=int(row.get('Validity', 0)),
                        time_ms=time_ms
                    )
                    records.append(record)
                except ValueError as e:
                    logger.error(f"Ошибка преобразования типов в строке {row} файла {filepath}: {e}")

    except Exception as e:
        logger.error(f"Критическая ошибка при чтении файла {filepath}: {e}")
        raise e  # Пробрасываем ошибку наверх, чтобы воркер отправил файл в папку failed

    return records


@dataclass
class RecipeRecord:
    panel_name: str
    recipe_name: str
    recipe_id: str
    side: str
    parameters: Dict[str, float]  # Словарь параметров (тег: значение)


# Функция парсинга рецепта
def parse_recipe(filepath: str, dec_symbol: str = ',', list_sep: str = ';') -> Optional[RecipeRecord]:
    """
    Парсит файл record_LEFT.csv или record_RIGHT.csv.
    Возвращает объект RecipeRecord или None, если файл битый.
    """
    try:
        with open(filepath, 'r', encoding='cp1251') as f:
            lines = f.readlines()

            if len(lines) < 5:
                logger.warning(f"Файл {filepath} слишком короткий.")
                return None

            # Строка 3 (индекс 2) - "LANGID_419;Recipe_data_record_6"
            header_line = lines[2].strip().split(list_sep)
            panel_name = header_line[0].strip() if len(header_line) > 0 else "Unknown_Panel"
            recipe_name = header_line[1].strip() if len(header_line) > 1 else "Unknown"

            # Строка 4 (индекс 3) - "1;6"
            id_line = lines[3].strip().split(list_sep)

            # 1 - левая, 2 - правая
            side_code = id_line[0].strip() if len(id_line) > 0 else "0"
            side = "LEFT" if side_code == "1" else "RIGHT" if side_code == "2" else "UNKNOWN"

            # Номер рецепта
            recipe_id = id_line[1].strip() if len(id_line) > 1 else "Unknown"

            params = {}
            # Данные начинаются с 5-й строки (индекс 4)
            for line in lines[4:]:
                parts = line.strip().split(list_sep)
                if len(parts) >= 2:
                    tag_name = parts[0]
                    # Заменяем десятичный разделитель на точку
                    value_str = parts[1].replace(dec_symbol, '.')
                    try:
                        value = float(value_str)
                        params[tag_name] = value
                    except ValueError:
                        pass  # Пропускаем строки, где значение не число

            return RecipeRecord(
                panel_name=panel_name,
                recipe_name=recipe_name,
                recipe_id=recipe_id,
                side=side,
                parameters=params
            )

    except Exception as e:
        logger.error(f"Критическая ошибка при чтении рецепта {filepath}: {e}")
        raise e