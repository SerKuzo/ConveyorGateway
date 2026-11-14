# core/database.py
import logging
import psycopg2
from psycopg2 import pool, Error
from config import settings
from core.parser import CounterRecord, RecipeRecord

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Менеджер подключений к PostgreSQL.
    Реализует паттерн Singleton для переиспользования пула соединений.
    """
    _instance = None
    _pool = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
            cls._instance._initialize_pool()
        return cls._instance

    def _initialize_pool(self):
        """Создает пул соединений при первом обращении."""
        try:
            self._pool = psycopg2.pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,  # Максимум 10 одновременных подключений (хватит с головой)
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                database=settings.DB_NAME,
                user=settings.DB_USER,
                password=settings.DB_PASSWORD
            )
            logger.info("Пул соединений с базой данных успешно инициализирован.")
        except Exception as e:
            logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось создать пул соединений к БД: {e}")
            raise

    def get_connection(self):
        """Берет соединение из пула."""
        if not self._pool:
            raise RuntimeError("Пул соединений не инициализирован!")
        return self._pool.getconn()

    def release_connection(self, conn):
        """Возвращает соединение в пул."""
        if self._pool and conn:
            self._pool.putconn(conn)

    def execute_transaction(self, query: str, params: tuple = None):
        """
        Выполняет запрос в транзакции (с автоматическим commit/rollback).
        Полезно для одиночных INSERT/UPDATE.
        """
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка транзакции: {e}")
            raise
        finally:
            self.release_connection(conn)

    # =========================================================================
    # МЕТОДЫ ДЛЯ РАБОТЫ С ДАННЫМИ (Repository Pattern)
    # =========================================================================

    def init_db(self):
        """Создает таблицы, если их нет. Вызывается при старте приложения."""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS counters
            (
                id SERIAL PRIMARY KEY,
                panel_name VARCHAR(50),
                var_name VARCHAR(50),
                time_string TIMESTAMP,
                var_value NUMERIC,
                validity INTEGER,
                time_ms VARCHAR(50),
                file_name VARCHAR(255),
                file_hash VARCHAR(64),
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(file_hash, var_name)
                );
            """,
            """
            CREATE TABLE IF NOT EXISTS recipes
            (
                id SERIAL PRIMARY KEY,
                panel_name VARCHAR(50),
                recipe_name VARCHAR(100),
                recipe_id VARCHAR(50),
                side VARCHAR(10),
                parameters JSONB,
                file_hash VARCHAR(64),
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(recipe_id, side, panel_name)
                );
            """
        ]

        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                for q in queries:
                    cur.execute(q)
            conn.commit()
            logger.info("Схема базы данных проверена/создана.")
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка инициализации БД: {e}")
            raise
        finally:
            self.release_connection(conn)

    def check_file_processed(self, file_hash: str, panel_name: str, file_type: str) -> bool:
        """Проверяет, есть ли уже такой хеш в базе (для пульса)."""
        table = "counters" if file_type == "counters" else "recipes"
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                # Мы ищем по хешу и имени панели, так как хеш может совпасть у разных панелей
                cur.execute(f"SELECT id FROM {table} WHERE file_hash = %s AND panel_name = %s LIMIT 1",
                            (file_hash, panel_name))
                return cur.fetchone() is not None
        finally:
            self.release_connection(conn)

    def update_heartbeat(self, file_hash: str, panel_name: str, file_type: str):
        """Обновляет время last_seen_at, если файл пришел повторно."""
        table = "counters" if file_type == "counters" else "recipes"
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE {table} SET last_seen_at = NOW() WHERE file_hash = %s AND panel_name = %s",
                            (file_hash, panel_name))
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка обновления heartbeat для {table}: {e}")
        finally:
            self.release_connection(conn)

    def insert_counters(self, panel_name: str, filename: str, file_hash: str, records: list[CounterRecord]):
        """Пакетная вставка счетчиков."""
        query = """
                INSERT INTO counters
                (panel_name, var_name, time_string, var_value, validity, time_ms, file_name, file_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (file_hash, var_name) DO NOTHING \
                """
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                for rec in records:
                    cur.execute(query, (
                        panel_name, rec.var_name, rec.time_string, rec.var_value,
                        rec.validity, rec.time_ms, filename, file_hash
                    ))
            conn.commit()
            logger.info(f"Вставлено {len(records)} записей счетчиков для панели {panel_name}")
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка вставки счетчиков: {e}")
            raise
        finally:
            self.release_connection(conn)

    def insert_recipe(self, panel_name: str, filename: str, file_hash: str, recipe: RecipeRecord):
        """Вставка/обновление рецепта."""
        import json
        query = """
                INSERT INTO recipes
                    (panel_name, recipe_name, recipe_id, side, parameters, file_hash)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (recipe_id, side, panel_name) 
            DO \
                UPDATE SET
                    recipe_name = EXCLUDED.recipe_name, \
                    parameters = EXCLUDED.parameters, \
                    file_hash = EXCLUDED.file_hash, \
                    last_seen_at = NOW() \
                """
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(query, (
                    panel_name, recipe.recipe_name, recipe.recipe_id,
                    recipe.side, json.dumps(recipe.parameters), file_hash
                ))
            conn.commit()
            logger.info(f"Рецепт {recipe.side} ({panel_name}) обновлен/вставлен.")
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка вставки рецепта: {e}")
            raise
        finally:
            self.release_connection(conn)


# Создаем глобальный экземпляр (Singleton)
db_manager = DatabaseManager()