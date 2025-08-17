# ContextVar-based Dependency Injection Migration

## Обзор изменений

Система dependency injection в fastsio была полностью переписана с использованием Python ContextVar для обеспечения thread-safe и async-safe управления зависимостями в рамках запроса.

## Основные улучшения

### 1. Thread-Safe и Async-Safe
- Использование `ContextVar` обеспечивает изоляцию зависимостей между запросами
- Поддержка concurrent обработки запросов без конфликтов
- Автоматическая очистка контекста после завершения обработчика

### 2. Расширенная поддержка зависимостей
- **Встроенные зависимости**: SocketID, Data, Event, Auth, Reason, Environ, AsyncServer
- **Кастомные зависимости**: через `Depends()` аналогично FastAPI
- **Pydantic модели**: автоматическая валидация как и раньше
- **Глобальные зависимости**: регистрация через `register_dependency()`

### 3. Кэширование зависимостей
- Автоматическое кэширование результатов в рамках одного запроса
- Предотвращение повторных вычислений дорогих операций
- Опциональное отключение кэширования через `use_cache=False`

## Новые возможности

### Depends() - аналог FastAPI

```python
from fastsio import AsyncServer, SocketID, Depends

# Простая зависимость
def get_database():
    return Database()

@sio.on("query")
async def handle_query(
    sid: SocketID,
    db: Database = Depends(get_database)
):
    result = await db.query("SELECT * FROM users")
    await sio.emit("result", result, to=sid)
```

### Зависимости с зависимостями

```python
async def get_db_connection():
    return await asyncpg.connect("postgresql://...")

async def get_user_service(db=Depends(get_db_connection)):
    return UserService(db)

@sio.on("get_user")
async def get_user(
    sid: SocketID,
    user_service=Depends(get_user_service)
):
    user = await user_service.get_user(user_id)
    await sio.emit("user", user.dict(), to=sid)
```

### Глобальная регистрация зависимостей

```python
from fastsio import register_dependency

# Регистрируем глобальную зависимость
register_dependency("config", lambda: AppConfig())

@sio.on("handler")
async def handler(
    sid: SocketID,
    config: AppConfig = Depends("config")  # Ссылка по имени
):
    pass
```

### Кэширование

```python
async def expensive_computation():
    # Дорогая операция
    await asyncio.sleep(1)
    return {"result": "computed"}

@sio.on("get_data")
async def get_data(
    sid: SocketID,
    data=Depends(expensive_computation)  # Кэшируется автоматически
):
    await sio.emit("data", data, to=sid)
```

## Обратная совместимость

Старый синтаксис dependency injection **полностью совместим**:

```python
# Работает как раньше, но теперь использует ContextVar
@sio.on("handler")
async def handler(sid: SocketID, server: AsyncServer, data: Data):
    pass
```

Система автоматически fallback'ается к старой реализации в случае проблем с новой системой.

## Технические детали

### Файлы изменений
- `src/fastsio/dependency.py` - новый модуль с ContextVar-based DI
- `src/fastsio/async_server.py` - обновлен `_trigger_event()` для использования новой системы
- `src/fastsio/types.py` - добавлен экспорт `Depends`
- `src/fastsio/__init__.py` - экспорт новых компонентов DI
- `docs/dependency_injection.rst` - обновлена документация

### Принцип работы
1. При вызове handler'а создается новый контекст ContextVar
2. Встроенные зависимости (SocketID, Data, etc.) устанавливаются в контекст
3. Кастомные зависимости разрешаются рекурсивно через их фабрики
4. Результаты кэшируются в рамках запроса
5. Контекст автоматически очищается после завершения

### Производительность
- Минимальный overhead по сравнению со старой системой
- Кэширование предотвращает повторные вычисления
- Lazy-loading зависимостей - разрешение только при необходимости

## Примеры использования

### Базовый пример
```python
from fastsio import AsyncServer, SocketID, Depends

sio = AsyncServer()

def get_config():
    return {"max_users": 100}

@sio.on("join")
async def join_handler(
    sid: SocketID,
    config: dict = Depends(get_config)
):
    if len(sio.manager.get_participants("/", "/")) >= config["max_users"]:
        await sio.emit("error", "Server full", to=sid)
        return
    
    await sio.emit("joined", {"status": "success"}, to=sid)
```

### Продвинутый пример с базой данных
```python
import asyncpg
from fastsio import AsyncServer, SocketID, Depends
from pydantic import BaseModel

class UserMessage(BaseModel):
    text: str
    room: str

# Глобальный пул соединений
db_pool = None

async def get_db():
    async with db_pool.acquire() as conn:
        yield conn

async def get_user_service(db=Depends(get_db)):
    return UserService(db)

@sio.on("send_message")
async def send_message(
    sid: SocketID,
    message: UserMessage,
    user_service=Depends(get_user_service)
):
    # Валидация Pydantic + DI в одном handler'е
    user = await user_service.get_user_by_sid(sid)
    if not user:
        await sio.emit("error", "User not found", to=sid)
        return
    
    await user_service.save_message(user.id, message.text, message.room)
    await sio.emit("message_saved", {"status": "ok"}, to=sid)
```

## Миграция

Никаких изменений в существующем коде не требуется! Новая система полностью обратно совместима.

Для использования новых возможностей просто добавьте `Depends()` в параметры handler'ов:

```python
# Старый код - работает как раньше
@sio.on("handler")
async def handler(sid: SocketID, data: Data):
    pass

# Новые возможности
@sio.on("handler")
async def handler(
    sid: SocketID, 
    data: Data,
    service: MyService = Depends(get_service)  # Добавляем новые зависимости
):
    pass
```

## Заключение

Новая система dependency injection на основе ContextVar предоставляет:
- ✅ Полную обратную совместимость
- ✅ Thread-safe и async-safe выполнение
- ✅ Расширенные возможности DI аналогично FastAPI
- ✅ Автоматическое кэширование
- ✅ Простую миграцию и использование

Система готова к production использованию и не требует изменений в существующем коде.
