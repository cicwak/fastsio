# fastsio (Fast Socket.IO for Python)

[![Build status](https://github.com/miguelgrinberg/python-socketio/workflows/build/badge.svg)](https://github.com/miguelgrinberg/python-socketio/actions) [![codecov](https://codecov.io/gh/miguelgrinberg/python-socketio/branch/main/graph/badge.svg)](https://codecov.io/gh/miguelgrinberg/python-socketio)

**fastsio** - это форк [python-socketio](https://github.com/miguelgrinberg/python-socketio) с улучшениями для современной разработки на Python, вдохновленными FastAPI.

## Основные особенности

- **Строгая типизация** и **FastAPI-подобный DX** с dependency injection
- **Автоматическая валидация** данных через Pydantic модели
- **Легковесные роутеры** для организации кода через `RouterSIO`
- **Совместимость** с существующими ASGI/WSGI стеками
- **Полная поддержка** протокола Socket.IO

## Установка

```bash
pip install fastsio
```

Для клиентской части:
```bash
pip install fastsio[client]
```

Для async клиента:
```bash
pip install fastsio[asyncio_client]
```

## Основные отличия от python-socketio

| Особенность | python-socketio | fastsio |
|-------------|-----------------|---------|
| **Типизация** | Минимальная | Строгая типизация с аннотациями |
| **Dependency Injection** | ❌ | ✅ FastAPI-стиль |
| **Pydantic валидация** | ❌ | ✅ Автоматическая |
| **Роутеры** | ❌ | ✅ RouterSIO для организации кода |
| **Аннотации параметров** | ❌ | ✅ `SocketID`, `Environ`, `Auth` и др. |
| **Совместимость** | ✅ | ✅ Полная обратная совместимость |

## Быстрый старт

### Простой сервер

```python
import fastsio
from fastsio import ASGIApp

# Создание сервера
sio = fastsio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*"
)

@sio.event
async def connect(sid, environ, auth):
    print(f"Клиент {sid} подключился")
    return True

@sio.event
async def disconnect(sid):
    print(f"Клиент {sid} отключился")

@sio.on("message")
async def handle_message(sid, data):
    await sio.emit("response", f"Получено: {data}", to=sid)

# ASGI приложение
app = ASGIApp(sio)
```

### С типизацией и dependency injection

```python
from pydantic import BaseModel
from fastsio import (
    AsyncServer, RouterSIO, SocketID, 
    Environ, Auth, ASGIApp
)

router = RouterSIO()

class Message(BaseModel):
    text: str
    room: str

class JoinRoom(BaseModel):
    room: str

@router.on("connect")
async def on_connect(
    sid: SocketID, 
    environ: Environ, 
    auth: Auth, 
    server: AsyncServer
):
    """Подключение с автоматической инъекцией зависимостей"""
    print(f"Подключение: {sid}, auth: {auth}")
    return True

@router.on("join_room")
async def on_join_room(
    sid: SocketID, 
    server: AsyncServer, 
    data: JoinRoom  # Автоматическая валидация Pydantic
):
    """Присоединение к комнате с валидацией данных"""
    await server.enter_room(sid, data.room)
    await server.emit("joined", {"room": data.room}, to=sid)

@router.on("send_message")
async def on_send_message(
    sid: SocketID, 
    server: AsyncServer, 
    data: Message
):
    """Отправка сообщения с валидацией"""
    await server.emit(
        "new_message", 
        data.model_dump(), 
        room=data.room
    )

# Создание сервера и подключение роутера
sio = AsyncServer(async_mode="asgi", cors_allowed_origins="*")
sio.add_router(router)

app = ASGIApp(sio)
```

### Интеграция с FastAPI

```python
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
import fastsio

app = FastAPI()
sio = fastsio.AsyncServer(async_mode="asgi")

@app.get("/")
async def index():
    return {"message": "FastAPI + fastsio"}

@sio.event
async def connect(sid, environ, auth):
    await sio.emit("hello", {"message": "Добро пожаловать!"}, to=sid)

# Объединение FastAPI и Socket.IO
combined_app = fastsio.ASGIApp(sio, app)

if __name__ == "__main__":
    uvicorn.run(combined_app, host="127.0.0.1", port=5000)
```

### Клиент

```python
import asyncio
import fastsio

sio = fastsio.AsyncClient()

@sio.event
async def connect():
    print("Подключено к серверу")
    await sio.emit("send_message", {
        "text": "Привет, сервер!",
        "room": "general"
    })

@sio.event
async def new_message(data):
    print(f"Новое сообщение: {data}")

@sio.event
async def disconnect():
    print("Отключено от сервера")

async def main():
    await sio.connect("http://localhost:5000")
    await sio.wait()

if __name__ == "__main__":
    asyncio.run(main())
```

## Расширенные возможности

### Роутеры для организации кода

```python
from fastsio import RouterSIO

# Роутер для чата
chat_router = RouterSIO(namespace="/chat")

@chat_router.on("message")
async def chat_message(sid: SocketID, data: Message):
    # Логика чата
    pass

# Роутер для админки
admin_router = RouterSIO(namespace="/admin")

@admin_router.on("user_ban")
async def ban_user(sid: SocketID, data: BanRequest):
    # Логика бана
    pass

# Подключение роутеров
sio.add_router(chat_router)
sio.add_router(admin_router)
```

### Доступные типы для injection

```python
from fastsio import (
    SocketID,    # ID подключения
    Environ,     # WSGI/ASGI environ
    Auth,        # Данные авторизации (только в connect)
    Reason,      # Причина отключения (только в disconnect)
    Data,        # Сырые данные события
    Event,       # Имя события
    AsyncServer  # Экземпляр сервера
)

@router.on("example")
async def example_handler(
    sid: SocketID,
    server: AsyncServer,
    environ: Environ,
    data: Data,
    event: Event
):
    print(f"Событие {event} от {sid}: {data}")
```

## Совместимость версий

Таблица совместимости с JavaScript Socket.IO:

| JavaScript Socket.IO | Socket.IO протокол | Engine.IO протокол | fastsio версия |
|---------------------|-------------------|-------------------|----------------|
| 0.9.x               | 1, 2              | 1, 2              | Не поддерживается |
| 1.x и 2.x           | 3, 4              | 3                 | 4.x            |
| 3.x и 4.x           | 5                 | 4                 | 5.x            |

## Документация

- [Полная документация](http://fastsio.readthedocs.io/)
- [PyPI](https://pypi.python.org/pypi/fastsio)
- [Changelog](https://github.com/cicwak/fastsio/blob/main/CHANGES.md)
- [Вопросы на Stack Overflow](https://stackoverflow.com/questions/tagged/python-socketio)

## Вклад в проект

fastsio основан на отличной работе [Miguel Grinberg](https://github.com/miguelgrinberg) над python-socketio. Мы добавили современные возможности, сохранив полную совместимость с оригинальным API.

## Лицензия

MIT License - см. файл [LICENSE](LICENSE)