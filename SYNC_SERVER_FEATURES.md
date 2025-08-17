# Синхронный сервер с полным набором функций

## Обзор

Синхронный сервер fastsio теперь поддерживает все продвинутые функции, которые ранее были доступны только в асинхронном сервере:

- ✅ **ContextVar-based Dependency Injection**
- ✅ **Pydantic валидация моделей**
- ✅ **AsyncAPI документация**
- ✅ **Кастомные зависимости через Depends()**
- ✅ **Thread-safe выполнение**

## Основные возможности

### 1. Dependency Injection

Синхронный сервер поддерживает все типы зависимостей:

```python
from fastsio import Server, SocketID, Data, Depends

sio = Server()

def get_database():
    return Database()

@sio.on("query")
def handle_query(
    sid: SocketID,
    server: Server,           # Встроенная зависимость
    data: Data,              # Встроенная зависимость  
    db=Depends(get_database) # Кастомная зависимость
):
    result = db.query(data)
    server.emit("result", result, to=sid)
```

### 2. Pydantic валидация

Автоматическая валидация входящих данных:

```python
from pydantic import BaseModel

class MessageData(BaseModel):
    text: str
    room: str
    priority: int = 1

@sio.on("send_message")
def send_message(
    sid: SocketID,
    server: Server,
    message: MessageData  # Автоматическая валидация
):
    # message уже провалидирован как MessageData
    server.emit("message_sent", {
        "text": message.text,
        "room": message.room
    }, room=message.room)
```

### 3. AsyncAPI документация

Полная поддержка AsyncAPI спецификации:

```python
sio = Server(
    asyncapi={
        "enabled": True,
        "title": "My Chat API",
        "version": "1.0.0",
        "description": "Chat server with rooms and validation",
        "servers": {
            "production": {
                "host": "api.example.com",
                "protocol": "socketio"
            }
        }
    }
)
```

Документация доступна по адресам:
- JSON: `/asyncapi.json`
- UI: `/asyncapi` (интерактивная документация)

### 4. Сложные зависимости

Зависимости могут зависеть от других зависимостей:

```python
def get_config():
    return AppConfig()

def get_database(config=Depends(get_config)):
    return Database(config.db_url)

def get_user_service(db=Depends(get_database)):
    return UserService(db)

@sio.on("get_user")
def get_user(
    sid: SocketID,
    server: Server,
    user_service=Depends(get_user_service)  # Цепочка зависимостей
):
    user = user_service.get_current_user(sid)
    server.emit("user_data", user.dict(), to=sid)
```

### 5. Глобальная регистрация зависимостей

```python
from fastsio import register_dependency

register_dependency("config", get_config)
register_dependency("database", get_database)

@sio.on("handler")
def handler(
    sid: SocketID,
    config=Depends("config"),    # По имени
    db=Depends("database")       # По имени
):
    pass
```

## Технические особенности

### Thread-Safe выполнение

Система использует ContextVar для изоляции зависимостей между запросами:

- Каждый запрос выполняется в собственном контексте
- Зависимости не пересекаются между потоками
- Автоматическая очистка после завершения обработки

### Производительность

- Минимальный overhead по сравнению с обычными вызовами
- Кэширование зависимостей в рамках запроса
- Lazy-loading - зависимости создаются только при необходимости

### Ограничения

- Синхронные handler'ы могут использовать только синхронные зависимости
- Нельзя использовать async функции в Depends() для sync handler'ов
- Циклические зависимости не поддерживаются

## Примеры использования

### Простой чат-сервер

```python
from fastsio import Server, SocketID, Depends
from pydantic import BaseModel
from typing import Dict, Set

class JoinRoom(BaseModel):
    room: str
    username: str

# Глобальное состояние (в реальном приложении - Redis/БД)
rooms: Dict[str, Set[str]] = {}

def get_room_manager():
    class RoomManager:
        def join_room(self, room: str, user: str):
            if room not in rooms:
                rooms[room] = set()
            rooms[room].add(user)
        
        def leave_room(self, room: str, user: str):
            if room in rooms:
                rooms[room].discard(user)
                if not rooms[room]:
                    del rooms[room]
        
        def get_users(self, room: str) -> Set[str]:
            return rooms.get(room, set())
    
    return RoomManager()

sio = Server(
    asyncapi={
        "enabled": True,
        "title": "Simple Chat",
        "version": "1.0.0"
    }
)

@sio.on("join")
def join_room(
    sid: SocketID,
    server: Server,
    data: JoinRoom,
    room_manager=Depends(get_room_manager)
):
    room_manager.join_room(data.room, data.username)
    server.enter_room(sid, data.room)
    
    users = room_manager.get_users(data.room)
    server.emit("user_joined", {
        "username": data.username,
        "room": data.room,
        "users_count": len(users)
    }, room=data.room)
```

### Интеграция с базой данных

```python
import sqlite3
from contextlib import contextmanager

@contextmanager
def get_db_connection():
    conn = sqlite3.connect("chat.db")
    try:
        yield conn
    finally:
        conn.close()

def get_user_service():
    class UserService:
        def get_user(self, user_id: str):
            with get_db_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM users WHERE id = ?", 
                    (user_id,)
                )
                return cursor.fetchone()
        
        def create_user(self, username: str):
            with get_db_connection() as conn:
                cursor = conn.execute(
                    "INSERT INTO users (username) VALUES (?)",
                    (username,)
                )
                conn.commit()
                return cursor.lastrowid
    
    return UserService()

@sio.on("register")
def register_user(
    sid: SocketID,
    server: Server,
    data: dict,
    user_service=Depends(get_user_service)
):
    user_id = user_service.create_user(data["username"])
    server.emit("registered", {"user_id": user_id}, to=sid)
```

## Развертывание

### С Gunicorn

```python
# app.py
from fastsio import Server, WSGIApp

sio = Server()
# ... настройка handlers

app = WSGIApp(sio)

# Запуск:
# gunicorn app:app --workers 4 --worker-class gevent
```

### С eventlet

```python
import eventlet
import eventlet.wsgi
from fastsio import Server, WSGIApp

sio = Server()
app = WSGIApp(sio)

if __name__ == "__main__":
    eventlet.wsgi.server(eventlet.listen(('', 5000)), app)
```

## Миграция с AsyncServer

Большинство кода можно переиспользовать:

```python
# Было (AsyncServer)
@sio.on("handler")
async def handler(sid: SocketID, server: AsyncServer, data: Data):
    result = await some_async_operation(data)
    await server.emit("result", result, to=sid)

# Стало (Server)  
@sio.on("handler")
def handler(sid: SocketID, server: Server, data: Data):
    result = some_sync_operation(data)  # Убираем await
    server.emit("result", result, to=sid)  # Убираем await
```

## Заключение

Синхронный сервер fastsio теперь предоставляет полный набор современных функций для создания производительных и хорошо структурированных Socket.IO приложений:

- **Простота использования**: Знакомый синтаксис без async/await
- **Мощные возможности**: Все функции AsyncServer
- **Производительность**: Thread-safe выполнение с минимальным overhead
- **Документация**: Автоматическая генерация AsyncAPI спецификации
- **Типизация**: Полная поддержка type hints и Pydantic валидации

Выбирайте синхронный сервер, если:
- Ваша логика преимущественно синхронная
- Используете традиционные WSGI серверы (Gunicorn, uWSGI)
- Предпочитаете простоту синхронного кода
- Интегрируетесь с существующими синхронными системами
