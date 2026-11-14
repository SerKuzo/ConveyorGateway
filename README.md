# Conveyor Gateway (Промышленный шлюз сбора данных)

Сервис предназначен для автоматического сбора, парсинга и сохранения в базу данных технологических файлов, поступающих с панелей оператора (WinCC) через сетевую папку Samba.

## 🏗️ Архитектура

Проект построен по принципам чистой архитектуры (Clean Architecture) и разделен на слои:

```text
conveyor_gateway/
├── config.py               # Конфигурация (Pydantic Settings + .env)
├── main.py                 # Точка входа (запуск, обработка сигналов ОС)
├── core/                   # Бизнес-логика
│   ├── database.py         # Работа с PostgreSQL (Connection Pool, Singleton)
│   └── parser.py           # Чистые функции парсинга CSV
├── services/               # Оркестрация
│   └── file_watcher.py     # Мониторинг папок (watchdog + Fallback Scanner)
├── tests/                  # Юнит-тесты
├── .env                    # Секреты (не в Git)
└── .env.example            # Шаблон секретов
```
### Ключевые особенности

* **Мониторинг:** Используется `watchdog` (`inotify`) для мгновенной реакции + фоновый сканер (Fallback).
* **Идемпотентность:** Защита от дублей реализована на уровне SQL (`ON CONFLICT DO NOTHING`) и проверки хеша файла (SHA-256).
* **Heartbeat:** Если панель присылает тот же файл (при простое оборудования), система не дублирует данные, а обновляет время `last_seen_at` в БД.
* **Отказоустойчивость:** Пул соединений к БД (Connection Pool), автоматический перезапуск при сбоях.

---

### 🛠️ Требования

* **ОС:** Ubuntu Server 24.04 LTS (или новее)
* **Python:** 3.10+
* **База данных:** PostgreSQL 16+ (рекомендуется запускать в Docker)
* **Сеть:** Настроенная Samba для приема файлов от панелей WinCC

## 🚀 Развертывание (Установка с нуля)

### 1. Клонирование репозитория
```bash
cd /opt
git clone git@github.com:SerKuzo/ConveyorGateway.git conveyor_gateway
cd conveyor_gateway
```

### 2. Настройка виртуального окружения
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Настройка конфигурации
Скопируйте шаблон и отредактируйте его (впишите реальные пароли и пути):

```bash
cp .env.example .env
nano .env
```

> ⚠️ **Важно:** В `DB_PASSWORD` нельзя использовать символ `$` без экранирования, если вы передаете его через переменные окружения (используйте `$$`).

### 4. Запуск Базы Данных (Docker)
Убедитесь, что Docker установлен, и поднимите контейнер с PostgreSQL:

```bash
docker compose up -d
```
*(Файл `docker-compose.yml` должен находиться в папке с проектом или рядом).*

### 5. Запуск как системная служба (Systemd)
Создайте файл службы:

```bash
sudo nano /etc/systemd/system/conveyor.service
```

Вставьте следующее содержимое:

```ini
[Unit]
Description=Conveyor Gateway Service
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/conveyor_gateway
ExecStart=/opt/conveyor_gateway/venv/bin/python -m main
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Активируйте и запустите службу:

```bash
sudo systemctl daemon-reload
sudo systemctl enable conveyor.service
sudo systemctl start conveyor.service
```

---

## 📊 Мониторинг и логирование

* **Просмотр логов в реальном времени:**
  ```bash
  sudo journalctl -u conveyor.service -f
  ```
* **Проверка статуса службы:**
  ```bash
  sudo systemctl status conveyor.service
  ```
* **Ротация логов:** В приложении настроен `RotatingFileHandler`, логи также дублируются в файл `/opt/conveyor_gateway/conveyor.log` (максимум 5 МБ, хранится 3 архивных копии).

---

## 🗄️ Структура Базы Данных

### Таблица `counters` (Счетчики)
Хранит данные из `CountData0.csv`: количество циклов, ходимость диафрагмы.

* `panel_name` — имя панели (например, `LANGID_419`).
* `var_name` — имя тега (например, `#V_L`).
* `var_value` — значение.
* `time_string` — время последнего изменения.
* `time_ms` — время последнего изменения в ms.
* `file_hash`  — хеш файла.
* `file_name`  — имя файла(`CountData0.csv`).
* `first_seen_at` — время, когда мы впервые увидели этот файл.
* `last_seen_at` — время последней обработки файла
 (когда панель присылает тот же самый файл (например, через 10 минут 
при простое оборудования) с тем же хешем скрипт не вставляет новые данные (чтобы не было дублей), 
скрипт выполняет команду UPDATE counters SET last_seen_at = NOW()).

### Таблица `recipes` (Рецепты)
Хранит текущие рецепты из `record_LEFT.csv` и `record_RIGHT.csv`.

* `panel_name` — имя панели.
* `side` — сторона (`LEFT` или `RIGHT`).
* `parameters` — `JSONB` объект со всеми параметрами рецепта.
* `file_hash`  — хеш файла.
* `first_seen_at` — время, когда мы впервые увидели этот файл.
* `last_seen_at` — время последней обработки файла
 (когда панель присылает тот же самый файл (например, через 10 минут 
при простое оборудования) с тем же хешем скрипт не вставляет новые данные (чтобы не было дублей), 
скрипт выполняет команду UPDATE counters SET last_seen_at = NOW()).
---

## 🧪 Тестирование

Для запуска юнит-тестов (проверка парсинга) выполните:

```bash
source venv/bin/activate
python -m pytest tests/
```

---

## 📁 Структура директорий (Файловая система)

* `/srv/conveyor_share/` — Входящая папка (сюда пишут панели).
* `/srv/conveyor_archive/` — Архив успешно обработанных файлов.
* `/srv/conveyor_failed/` — Битые файлы, вызвавшие ошибку.

---

## ⚠️ Известные ограничения

* Файлы `ALL_Рецепт левый/правый.csv` пока только логируются и не сохраняются в БД. Для их обработки потребуется создать отдельную таблицу-справочник.
