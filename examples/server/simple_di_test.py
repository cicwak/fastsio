"""
Simple test example for ContextVar-based dependency injection.

This demonstrates basic usage of the new DI system.
"""

from fastsio import AsyncServer, SocketID, Data, Event, Depends
from pydantic import BaseModel


# Configuration dependency
class Config:
    def __init__(self):
        self.app_name = "FastSIO DI Test"
        self.version = "1.0.0"


def get_config():
    return Config()


# Simple service dependency
class MessageService:
    def __init__(self):
        self.message_count = 0
    
    def process_message(self, message: str) -> str:
        self.message_count += 1
        return f"[{self.message_count}] Processed: {message}"


def get_message_service():
    return MessageService()


# Pydantic model
class ChatMessage(BaseModel):
    text: str
    room: str = "general"


# Create server
sio = AsyncServer()


@sio.event
async def connect(
    sid: SocketID,
    config: Config = Depends(get_config)
):
    """Test connect with dependency injection."""
    print(f"Connected to {config.app_name} v{config.version}: {sid}")
    return True


@sio.event
async def disconnect(sid: SocketID):
    """Test disconnect."""
    print(f"Disconnected: {sid}")


@sio.on("test_message")
async def test_message(
    sid: SocketID,
    event: Event,
    data: Data,
    config: Config = Depends(get_config),
    service: MessageService = Depends(get_message_service)
):
    """Test message handler with multiple dependencies."""
    print(f"Event: {event}, SID: {sid}")
    print(f"Config: {config.app_name}")
    print(f"Raw data: {data}")
    
    if isinstance(data, dict) and "message" in data:
        processed = service.process_message(data["message"])
        await sio.emit("response", {"processed": processed}, to=sid)
    else:
        await sio.emit("error", {"message": "Invalid data format"}, to=sid)


@sio.on("chat")
async def chat_message(
    sid: SocketID,
    message: ChatMessage,
    service: MessageService = Depends(get_message_service)
):
    """Test Pydantic model validation with DI."""
    processed = service.process_message(message.text)
    
    await sio.emit("chat_response", {
        "room": message.room,
        "original": message.text,
        "processed": processed
    }, to=sid)


if __name__ == "__main__":
    print("Simple DI test server ready")
    print("Test events: test_message, chat")
