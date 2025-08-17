"""
Example demonstrating ContextVar-based dependency injection in fastsio.

This example shows how to use the new dependency injection system with:
- Built-in dependencies (SocketID, Data, etc.)
- Custom dependencies with Depends()
- Database connections
- Configuration objects
- Caching
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Dict, Optional

from fastsio import AsyncServer, SocketID, Data, Depends, register_dependency
from pydantic import BaseModel


# Configuration
@dataclass
class AppConfig:
    max_connections: int = 100
    allow_anonymous: bool = True
    cache_ttl: int = 300


# Pydantic models for data validation
class JoinRoomMessage(BaseModel):
    room: str
    password: Optional[str] = None


class SendMessage(BaseModel):
    room: str
    message: str


class UserProfile(BaseModel):
    user_id: str
    username: str
    email: str


# Global state (in real app, these would be proper services)
app_config = AppConfig(max_connections=50, allow_anonymous=False)
fake_database: Dict[str, UserProfile] = {
    "user1": UserProfile(user_id="user1", username="alice", email="alice@example.com"),
    "user2": UserProfile(user_id="user2", username="bob", email="bob@example.com"),
}
cache_store: Dict[str, str] = {}


# Dependency factories
def get_config() -> AppConfig:
    """Get application configuration."""
    return app_config


async def get_database():
    """Mock database connection."""
    # In real app, this would be a proper database connection
    print("🔌 Getting database connection")
    return fake_database


async def get_cache():
    """Mock cache connection."""
    print("📦 Getting cache connection")
    return cache_store


async def get_user_service(db=Depends(get_database)):
    """User service that depends on database."""
    class UserService:
        def __init__(self, db):
            self.db = db
        
        async def get_user(self, user_id: str) -> Optional[UserProfile]:
            return self.db.get(user_id)
        
        async def user_exists(self, user_id: str) -> bool:
            return user_id in self.db
    
    return UserService(db)


async def get_room_service(cache=Depends(get_cache)):
    """Room service with caching."""
    class RoomService:
        def __init__(self, cache):
            self.cache = cache
            self.rooms: Dict[str, set] = {}
        
        async def join_room(self, room: str, user_id: str):
            if room not in self.rooms:
                self.rooms[room] = set()
            self.rooms[room].add(user_id)
            
            # Cache room info
            cache_key = f"room:{room}"
            room_info = {
                "name": room,
                "members": len(self.rooms[room]),
                "users": list(self.rooms[room])
            }
            self.cache[cache_key] = json.dumps(room_info)
        
        async def leave_room(self, room: str, user_id: str):
            if room in self.rooms:
                self.rooms[room].discard(user_id)
                if not self.rooms[room]:
                    del self.rooms[room]
        
        async def get_room_info(self, room: str):
            cache_key = f"room:{room}"
            if cache_key in self.cache:
                return json.loads(self.cache[cache_key])
            return None
    
    return RoomService(cache)


# Register global dependencies
register_dependency("config", get_config)
register_dependency("database", get_database)


# Create server and router
sio = AsyncServer(cors_allowed_origins="*")


@sio.event
async def connect(
    sid: SocketID,
    config: AppConfig = Depends(get_config),
    user_service=Depends(get_user_service)
):
    """Handle client connection with dependency injection."""
    print(f"🔗 Client {sid} connecting...")
    
    # Check connection limits
    if len(sio.manager.get_participants("/", "/")) >= config.max_connections:
        print(f"❌ Connection limit reached for {sid}")
        return False
    
    if not config.allow_anonymous:
        print(f"❌ Anonymous connections not allowed for {sid}")
        return False
    
    print(f"✅ Client {sid} connected successfully")
    return True


@sio.event
async def disconnect(sid: SocketID):
    """Handle client disconnection."""
    print(f"👋 Client {sid} disconnected")


@sio.on("join_room")
async def join_room(
    sid: SocketID,
    data: JoinRoomMessage,
    room_service=Depends(get_room_service),
    user_service=Depends(get_user_service)
):
    """Join a room with validation and dependency injection."""
    print(f"🏠 {sid} wants to join room: {data.room}")
    
    # Mock user ID (in real app, get from auth)
    user_id = f"user_{sid[:8]}"
    
    # Join room using service
    await room_service.join_room(data.room, user_id)
    await sio.enter_room(sid, data.room)
    
    # Get room info
    room_info = await room_service.get_room_info(data.room)
    
    # Notify room members
    await sio.emit(
        "user_joined",
        {
            "user_id": user_id,
            "room": data.room,
            "room_info": room_info
        },
        room=data.room
    )
    
    # Confirm to user
    await sio.emit("joined_room", {"room": data.room, "room_info": room_info}, to=sid)


@sio.on("send_message")
async def send_message(
    sid: SocketID,
    data: SendMessage,
    user_service=Depends(get_user_service),
    config: AppConfig = Depends("config")  # Using registered dependency
):
    """Send message to room with dependency injection."""
    print(f"💬 {sid} sending message to {data.room}: {data.message}")
    
    # Mock user ID
    user_id = f"user_{sid[:8]}"
    
    # Get user info
    user = await user_service.get_user(user_id)
    username = user.username if user else f"Anonymous_{sid[:8]}"
    
    # Send message to room
    await sio.emit(
        "new_message",
        {
            "room": data.room,
            "message": data.message,
            "user_id": user_id,
            "username": username,
            "timestamp": asyncio.get_event_loop().time()
        },
        room=data.room
    )


@sio.on("get_profile")
async def get_profile(
    sid: SocketID,
    data: Data,
    user_service=Depends(get_user_service)
):
    """Get user profile with dependency injection."""
    user_id = data.get("user_id") if isinstance(data, dict) else None
    
    if not user_id:
        await sio.emit("error", {"message": "user_id required"}, to=sid)
        return
    
    user = await user_service.get_user(user_id)
    if user:
        await sio.emit("profile", user.dict(), to=sid)
    else:
        await sio.emit("error", {"message": "User not found"}, to=sid)


@sio.on("room_info")
async def get_room_info(
    sid: SocketID,
    data: Data,
    room_service=Depends(get_room_service)
):
    """Get room information with caching."""
    room = data.get("room") if isinstance(data, dict) else None
    
    if not room:
        await sio.emit("error", {"message": "room required"}, to=sid)
        return
    
    room_info = await room_service.get_room_info(room)
    if room_info:
        await sio.emit("room_info", room_info, to=sid)
    else:
        await sio.emit("error", {"message": "Room not found"}, to=sid)


if __name__ == "__main__":
    print("🚀 Starting SocketIO server with ContextVar dependency injection...")
    print("📚 Available events:")
    print("  - join_room: Join a chat room")
    print("  - send_message: Send message to room")
    print("  - get_profile: Get user profile")
    print("  - room_info: Get room information")
    print("\n🔧 Features demonstrated:")
    print("  - ContextVar-based dependency injection")
    print("  - Custom dependencies with Depends()")
    print("  - Database service injection")
    print("  - Configuration injection")
    print("  - Caching service")
    print("  - Pydantic model validation")
    print("  - Global dependency registration")
    
    # Note: This is just the server setup
    # In a real application, you would run this with a web framework like FastAPI
    print("\n⚠️  To run this server, integrate it with a web framework:")
    print("  from fastsio import ASGIApp")
    print("  app = ASGIApp(sio)")
    print("  # Then run with uvicorn app:app")
