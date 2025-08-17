"""
Полный пример синхронного сервера с всеми новыми функциями:
- ContextVar-based Dependency Injection
- Pydantic валидация
- AsyncAPI документация
- Кастомные зависимости
"""

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from fastsio import Server, SocketID, Data, Depends, register_dependency
from pydantic import BaseModel


# Конфигурация приложения
@dataclass
class AppConfig:
    max_connections: int = 100
    allow_anonymous: bool = True
    message_rate_limit: int = 10  # сообщений в минуту


# Pydantic модели для валидации
class JoinRoomMessage(BaseModel):
    room: str
    password: Optional[str] = None


class SendMessage(BaseModel):
    room: str
    message: str
    timestamp: Optional[float] = None


class UserProfile(BaseModel):
    user_id: str
    username: str
    email: str


# Глобальные сервисы (в реальном приложении это были бы настоящие сервисы)
app_config = AppConfig(max_connections=50, allow_anonymous=True)
fake_database: Dict[str, UserProfile] = {
    "user1": UserProfile(user_id="user1", username="alice", email="alice@example.com"),
    "user2": UserProfile(user_id="user2", username="bob", email="bob@example.com"),
}
message_cache: Dict[str, list] = {}
connection_count = 0
connection_lock = threading.Lock()


# Фабрики зависимостей
def get_config() -> AppConfig:
    """Получить конфигурацию приложения."""
    return app_config


def get_database():
    """Получить подключение к базе данных (mock)."""
    print("🔌 Getting database connection")
    return fake_database


def get_cache():
    """Получить кеш (mock)."""
    print("📦 Getting cache connection")
    return message_cache


def get_user_service(db=Depends(get_database)):
    """Сервис пользователей с зависимостью от БД."""
    class UserService:
        def __init__(self, db):
            self.db = db
        
        def get_user(self, user_id: str) -> Optional[UserProfile]:
            return self.db.get(user_id)
        
        def user_exists(self, user_id: str) -> bool:
            return user_id in self.db
    
    return UserService(db)


def get_room_service(cache=Depends(get_cache)):
    """Сервис комнат с кешированием."""
    class RoomService:
        def __init__(self, cache):
            self.cache = cache
            self.rooms: Dict[str, set] = {}
        
        def join_room(self, room: str, user_id: str):
            if room not in self.rooms:
                self.rooms[room] = set()
            self.rooms[room].add(user_id)
            
            # Кешируем информацию о комнате
            if room not in self.cache:
                self.cache[room] = []
        
        def leave_room(self, room: str, user_id: str):
            if room in self.rooms:
                self.rooms[room].discard(user_id)
                if not self.rooms[room]:
                    del self.rooms[room]
        
        def get_room_members(self, room: str) -> set:
            return self.rooms.get(room, set())
        
        def add_message(self, room: str, message: str, user: str):
            if room not in self.cache:
                self.cache[room] = []
            self.cache[room].append({
                "message": message,
                "user": user,
                "timestamp": time.time()
            })
            # Ограничиваем историю последними 100 сообщениями
            if len(self.cache[room]) > 100:
                self.cache[room] = self.cache[room][-100:]
    
    return RoomService(cache)


# Регистрируем глобальные зависимости
register_dependency("config", get_config)
register_dependency("database", get_database)


# Создаем сервер с AsyncAPI документацией
sio = Server(
    asyncapi={
        "enabled": True,
        "title": "Sync Chat Server API",
        "version": "1.0.0",
        "description": "Синхронный чат-сервер с поддержкой комнат, валидацией сообщений и dependency injection",
        "servers": {
            "development": {
                "host": "localhost:5000",
                "protocol": "socketio",
                "description": "Development server"
            }
        }
    }
)


@sio.event
def connect(
    sid: SocketID,
    config: AppConfig = Depends(get_config),
    user_service=Depends(get_user_service)
):
    """Обработка подключения клиента с проверкой лимитов."""
    global connection_count
    
    print(f"🔗 Client {sid} attempting to connect...")
    
    with connection_lock:
        if connection_count >= config.max_connections:
            print(f"❌ Connection limit reached for {sid}")
            return False
        
        if not config.allow_anonymous:
            print(f"❌ Anonymous connections not allowed for {sid}")
            return False
        
        connection_count += 1
        print(f"✅ Client {sid} connected successfully. Total: {connection_count}")
        return True


@sio.event
def disconnect(sid: SocketID):
    """Обработка отключения клиента."""
    global connection_count
    
    with connection_lock:
        connection_count -= 1
        print(f"👋 Client {sid} disconnected. Total: {connection_count}")


@sio.on("join_room")
def join_room(
    sid: SocketID,
    data: JoinRoomMessage,
    server: Server,
    room_service=Depends(get_room_service),
    user_service=Depends(get_user_service)
):
    """Присоединиться к комнате с валидацией."""
    print(f"🏠 {sid} wants to join room: {data.room}")
    
    # Mock user ID (в реальном приложении получаем из аутентификации)
    user_id = f"user_{sid[:8]}"
    
    # Присоединяемся к комнате
    room_service.join_room(data.room, user_id)
    server.enter_room(sid, data.room)
    
    # Получаем участников комнаты
    members = room_service.get_room_members(data.room)
    
    # Уведомляем участников комнаты
    server.emit(
        "user_joined",
        {
            "user_id": user_id,
            "room": data.room,
            "members_count": len(members)
        },
        room=data.room
    )
    
    # Подтверждаем пользователю
    server.emit("joined_room", {
        "room": data.room,
        "members_count": len(members),
        "status": "success"
    }, to=sid)


@sio.on("send_message")
def send_message(
    sid: SocketID,
    data: SendMessage,
    server: Server,
    user_service=Depends(get_user_service),
    room_service=Depends(get_room_service),
    config: AppConfig = Depends("config")  # Используем зарегистрированную зависимость
):
    """Отправить сообщение в комнату с валидацией."""
    print(f"💬 {sid} sending message to {data.room}: {data.message}")
    
    # Mock user ID
    user_id = f"user_{sid[:8]}"
    
    # Получаем информацию о пользователе
    user = user_service.get_user(user_id)
    username = user.username if user else f"Anonymous_{sid[:8]}"
    
    # Добавляем сообщение в историю
    room_service.add_message(data.room, data.message, username)
    
    # Отправляем сообщение в комнату
    server.emit(
        "new_message",
        {
            "room": data.room,
            "message": data.message,
            "user_id": user_id,
            "username": username,
            "timestamp": data.timestamp or time.time()
        },
        room=data.room
    )


@sio.on("get_profile")
def get_profile(
    sid: SocketID,
    data: Data,
    server: Server,
    user_service=Depends(get_user_service)
):
    """Получить профиль пользователя."""
    if not isinstance(data, dict) or "user_id" not in data:
        server.emit("error", {"message": "user_id required"}, to=sid)
        return
    
    user_id = data["user_id"]
    user = user_service.get_user(user_id)
    
    if user:
        server.emit("profile", user.dict(), to=sid)
    else:
        server.emit("error", {"message": "User not found"}, to=sid)


@sio.on("get_room_history")
def get_room_history(
    sid: SocketID,
    data: Data,
    server: Server,
    room_service=Depends(get_room_service)
):
    """Получить историю сообщений комнаты."""
    if not isinstance(data, dict) or "room" not in data:
        server.emit("error", {"message": "room required"}, to=sid)
        return
    
    room = data["room"]
    cache = room_service.cache
    
    history = cache.get(room, [])
    server.emit("room_history", {
        "room": room,
        "messages": history[-20:]  # Последние 20 сообщений
    }, to=sid)


@sio.on("get_stats")
def get_stats(
    sid: SocketID,
    server: Server,
    config: AppConfig = Depends(get_config)
):
    """Получить статистику сервера."""
    server.emit("stats", {
        "connected_clients": connection_count,
        "max_connections": config.max_connections,
        "server_type": "synchronous",
        "features": [
            "ContextVar DI",
            "Pydantic Validation", 
            "AsyncAPI Documentation",
            "Custom Dependencies"
        ]
    }, to=sid)


if __name__ == "__main__":
    print("🚀 Starting Synchronous SocketIO Server with Full Features...")
    print("📚 Available events:")
    print("  - join_room: Join a chat room")
    print("  - send_message: Send message to room")
    print("  - get_profile: Get user profile")
    print("  - get_room_history: Get room message history")
    print("  - get_stats: Get server statistics")
    print("\n🔧 Features:")
    print("  - ✅ ContextVar-based dependency injection")
    print("  - ✅ Pydantic model validation")
    print("  - ✅ AsyncAPI documentation")
    print("  - ✅ Custom dependencies with Depends()")
    print("  - ✅ Global dependency registration")
    print("  - ✅ Thread-safe operations")
    print("  - ✅ Room management")
    print("  - ✅ Message history")
    print("  - ✅ Connection limits")
    
    print("\n📖 AsyncAPI Documentation:")
    print(f"  - JSON: {sio.asyncapi_config.url}")
    print(f"  - UI: {sio.asyncapi_config.ui_url}")
    
    print("\n⚠️  To run this server, integrate it with a web framework:")
    print("  from fastsio import WSGIApp")
    print("  app = WSGIApp(sio)")
    print("  # Then run with gunicorn or similar WSGI server")
    print("\n  Example with eventlet:")
    print("  import eventlet")
    print("  import eventlet.wsgi")
    print("  eventlet.wsgi.server(eventlet.listen(('', 5000)), app)")
    
    # Для демонстрации можем показать AsyncAPI конфигурацию
    print(f"\n🔧 Server configuration:")
    print(f"  Max connections: {app_config.max_connections}")
    print(f"  Allow anonymous: {app_config.allow_anonymous}")
    print(f"  Message rate limit: {app_config.message_rate_limit}/min")
    print(f"  AsyncAPI enabled: {sio.asyncapi_config.enabled}")
    print(f"  AsyncAPI title: {sio.asyncapi_config.title}")
