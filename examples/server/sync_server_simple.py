"""
Простой пример синхронного сервера с новыми функциями.
"""

from fastsio import Server, SocketID, Data, Depends
from pydantic import BaseModel


# Конфигурация
class Config:
    def __init__(self):
        self.app_name = "Simple Sync Server"
        self.version = "1.0.0"


def get_config():
    return Config()


# Простой сервис
class MessageService:
    def __init__(self):
        self.message_count = 0
    
    def process_message(self, message: str) -> str:
        self.message_count += 1
        return f"[{self.message_count}] Processed: {message}"


def get_message_service():
    return MessageService()


# Pydantic модель
class ChatMessage(BaseModel):
    text: str
    room: str = "general"


# Создаем сервер с AsyncAPI
sio = Server(
    asyncapi={
        "enabled": True,
        "title": "Simple Sync Chat",
        "version": "1.0.0"
    }
)


@sio.event
def connect(
    sid: SocketID,
    config: Config = Depends(get_config)
):
    """Подключение с dependency injection."""
    print(f"Connected to {config.app_name} v{config.version}: {sid}")
    return True


@sio.event
def disconnect(sid: SocketID):
    """Отключение."""
    print(f"Disconnected: {sid}")


@sio.on("test_message")
def test_message(
    sid: SocketID,
    data: Data,
    server: Server,
    config: Config = Depends(get_config),
    service: MessageService = Depends(get_message_service)
):
    """Тестовое сообщение с множественными зависимостями."""
    print(f"Config: {config.app_name}")
    print(f"Raw data: {data}")
    
    if isinstance(data, dict) and "message" in data:
        processed = service.process_message(data["message"])
        server.emit("response", {"processed": processed}, to=sid)
    else:
        server.emit("error", {"message": "Invalid data format"}, to=sid)


@sio.on("chat")
def chat_message(
    sid: SocketID,
    message: ChatMessage,
    server: Server,
    service: MessageService = Depends(get_message_service)
):
    """Чат с Pydantic валидацией и DI."""
    processed = service.process_message(message.text)
    
    server.emit("chat_response", {
        "room": message.room,
        "original": message.text,
        "processed": processed
    }, to=sid)


if __name__ == "__main__":
    print("🚀 Simple Sync Server with DI + Pydantic + AsyncAPI")
    print("📚 Test events: test_message, chat")
    print("📖 AsyncAPI: /asyncapi.json")
    print("\n⚠️  Run with WSGI server:")
    print("  from fastsio import WSGIApp")
    print("  app = WSGIApp(sio)")
    print("  # gunicorn app:app")
