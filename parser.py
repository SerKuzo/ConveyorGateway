import csv
import os
from dotenv import load_dotenv
from typing import LiteralString

import psycopg2
import hashlib
from datetime import datetime

import logging
logger = logging.getLogger(__name__)

# Загружаем переменные из файла .env
load_dotenv()
# Настройки БД
DB_CONFIG = {
    "dbname":   os.getenv("DB_NAME"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "host":     os.getenv("DB_HOST"),
    "port":     os.getenv("DB_PORT")
}


def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Таблица для счетчиков
            cur.execute("""
                CREATE TABLE IF NOT EXISTS counters (
                    id SERIAL PRIMARY KEY,
                    var_name VARCHAR(50),
                    time_string TIMESTAMP,
                    var_value NUMERIC,
                    validity INTEGER,
                    time_ms VARCHAR(50),
                    file_name VARCHAR(255),
                    file_hash VARCHAR(64), -- Хеш содержимого
                    panel_name VARCHAR(50),
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- Когда впервые увидели
                    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- Когда последний раз "пульс"
                    UNIQUE(file_hash, var_name) -- Защита от дублей
                );
            """)
            # Таблица для рецептов
            cur.execute("""
                        CREATE TABLE IF NOT EXISTS recipes
                        (
                            id SERIAL PRIMARY KEY,
                            panel_name VARCHAR(50), -- Имя панели (LANGID_419)
                            recipe_name VARCHAR(100),
                            recipe_id VARCHAR(50),
                            side VARCHAR(10), -- LEFT или RIGHT
                            parameters JSONB,
                            file_hash VARCHAR(64),
                            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(recipe_id, side, panel_name) -- Добавили panel_name в уникальность
                            );
                        """)
            conn.commit()


def get_csv_settings(filepath):
    """
    Читает первую строку файла и возвращает (list_separator, decimal_symbol).
    По умолчанию возвращает (';', ','), если настройки не найдены.
    """
    default_sep = ';'
    default_dec = ','

    try:
        with open(filepath, 'r', encoding='cp1251') as f:
            first_line = f.readline().strip()

        # Ожидаемый формат: "List separator=;Decimal symbol=,"
        if "List separator=" in first_line and "Decimal symbol=" in first_line:
            # Разбиваем по "Decimal symbol="
            parts = first_line.split("Decimal symbol=")

            # Левая часть: "List separator=;"
            list_part = parts[0].strip()
            list_sep = list_part.replace("List separator=", "").strip()

            # Правая часть: ","
            dec_symbol = parts[1].strip()

            return list_sep, dec_symbol

    except Exception as e:
        print(f"[Warning] Не удалось прочитать настройки разделителей из {filepath}: {e}")

    return default_sep, default_dec


def parse_counters(filepath):
    """Парсит CountData0.csv"""
    list_sep, dec_symbol = get_csv_settings(filepath)

    # Используем кодировку cp1251, так как WinCC часто выдает ее
    with open(filepath, 'r', encoding='cp1251') as f:
        reader = csv.DictReader(f, delimiter=list_sep)

        # WinCC иногда ставит кавычки в заголовках, убираем их
        reader.fieldnames = [name.strip('"') for name in reader.fieldnames]

        rows = []
        for row in reader:
            if not row.get('VarName'): continue

            # Конвертация десятичного разделителя для Time_ms
            time_ms = row.get('Time_ms', '').replace(dec_symbol, '.')
            # Конвертация значения
            value_str = row.get('VarValue', '0').replace(dec_symbol, '.')

            # Парсинг времени (формат WinCC: 17.08.2026 9:09:50)
            time_str = row.get('TimeString')
            dt = None
            if time_str:
                try:
                    dt = datetime.strptime(time_str, "%d.%m.%Y %H:%M:%S")
                except ValueError:
                    pass

            rows.append((
                row.get('VarName'),
                dt,
                float(value_str),
                int(row.get('Validity', 0)),
                time_ms
            ))
        return rows


def parse_recipe(filepath):
    """Парсит record_LEFT.csv или record_RIGHT.csv"""
    list_sep, dec_symbol = get_csv_settings(filepath)

    with open(filepath, 'r', encoding='cp1251') as f:
        lines = f.readlines()

        if len(lines) < 5: return None

        # Строка 3 (индекс 2) - "LANGID_419;Recipe_data_record_6"
        header_line = lines[2].strip().split(list_sep)
        panel_name = header_line[0].strip() if len(header_line) > 0 else "Unknown_Panel"

        # Строка 4 (индекс 3) - "1;6"
        id_line = lines[3].strip().split(list_sep)

        # 1 - левая, 2 - правая
        side_code = id_line[0].strip() if len(id_line) > 0 else "0"
        side = "LEFT" if side_code == "1" else "RIGHT" if side_code == "2" else "UNKNOWN"

        # Номер рецепта
        recipe_id = id_line[1].strip() if len(id_line) > 1 else "Unknown"

        # Имя рецепта
        recipe_name = header_line[1].strip() if len(header_line) > 1 else "Unknown"

        params = []
        # Данные начинаются с 5-й строки (индекс 4)
        for line in lines[4:]:
            parts = line.strip().split(list_sep)
            if len(parts) >= 2:
                tag_name = parts[0]
                # Заменяем десятичный разделитель на точку
                value_str = parts[1].replace(dec_symbol, '.')
                try:
                    value = float(value_str)
                    params.append((tag_name, value))
                except ValueError:
                    pass

        return panel_name, recipe_name, recipe_id, side, params


def calculate_hash(filepath):
    """Вычисляет SHA-256 хеш файла"""
    hash_sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4095), b""):
            hash_sha256.update(chunk)

    # print(hash_sha256.hexdigest(), filepath)
    return hash_sha256.hexdigest()


def process_file(filepath):
    filename = os.path.basename(filepath)

    # ИЗВЛЕКАЕМ ИМЯ ПАНЕЛИ ИЗ ПУТИ
    # filepath = /srv/conveyor_share/LANGID_419/record_LEFT.csv
    # os.path.dirname(filepath) = /srv/conveyor_share/LANGID_419
    # os.path.basename(...) = LANGID_419
    panel_dir: str | bytes | LiteralString = os.path.dirname(filepath)
    panel_name = os.path.basename(panel_dir)

    # Если файл лежит прямо в корне (без папки), ставим Unknown
    if panel_name == "conveyor_share":
        panel_name = "Unknown_Panel"
        
    file_hash = calculate_hash(filepath)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            try:
                if "CountData" in filename:
                    # Проверяем, есть ли уже такой хеш учитывая панель
                    cur.execute("SELECT id FROM counters WHERE file_hash = %s AND panel_name = %s LIMIT 1", (file_hash, panel_name))
                    existing = cur.fetchone()

                    if existing:
                        # Файл не изменился — просто обновляем пульс
                        cur.execute(""" UPDATE counters SET last_seen_at = NOW() 
                            WHERE file_hash = %s AND panel_name = %s""", (file_hash, panel_name))
                        logger.info(f"[Heartbeat] Счетчики не изменились для {panel_name}: {filename}")
                    else:
                        # Новые данные — парсим и вставляем
                        data = parse_counters(filepath)
                        if not data:
                            return

                        for row in data:
                            cur.execute("""
                                        INSERT INTO counters (panel_name, var_name, time_string, var_value, validity,
                                                              time_ms, file_name, file_hash)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s,
                                                %s) ON CONFLICT (file_hash, var_name) DO NOTHING
                                        """, (panel_name, *row, filename, file_hash))
                        logger.info(f"[New Data] Добавлены новые счетчики для {panel_name}: {filename}")


                elif "record_" in filename:

                    # Получаем все данные из файла

                    parse_result = parse_recipe(filepath)

                    if not parse_result:
                        logger.error(f"[Error] Ошибка при обработке {filename}: {e}")

                        return

                    _, recipe_name, recipe_id, side, params = parse_result

                    # Проверяем, менялся ли рецепт для ЭТОЙ панели и ЭТОЙ стороны

                    cur.execute("""

                                SELECT file_hash
                                FROM recipes

                                WHERE recipe_id = %s
                                  AND side = %s
                                  AND panel_name = %s

                                """, (recipe_id, side, panel_name))

                    existing = cur.fetchone()

                    if existing and existing[0] == file_hash:

                        # Рецепт тот же — пульс

                        cur.execute("""

                                    UPDATE recipes
                                    SET last_seen_at = NOW()

                                    WHERE recipe_id = %s
                                      AND side = %s
                                      AND panel_name = %s

                                    """, (recipe_id, side, panel_name))

                        logger.info(f"[Heartbeat] Рецепт {side} ({panel_name}) не изменился: {filename}")

                    else:

                        # Рецепт изменился — обновляем данные

                        import json

                        cur.execute("""
                                    INSERT INTO recipes (panel_name, recipe_name, recipe_id, side, parameters, file_hash)
                                    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (recipe_id, side, panel_name) 
                                    DO
                                    UPDATE SET
                                        recipe_name = EXCLUDED.recipe_name,
                                        parameters = EXCLUDED.parameters,
                                        file_hash = EXCLUDED.file_hash,
                                        last_seen_at = NOW()
                                    """, (panel_name, recipe_name, recipe_id, side, json.dumps(params), file_hash))

                        logger.info(f"[Updated] Рецепт {side} ({panel_name}) обновлен: {filename}")

                conn.commit()

            except Exception as e:
                conn.rollback()
                logger.error(f"[Error] Ошибка при обработке {filename}: {e}")
                raise e


if __name__ == "__main__":
    init_db()
    # Тестовый запуск на конкретном файле
    # process_file('/srv/conveyor_share/CountData0.csv')