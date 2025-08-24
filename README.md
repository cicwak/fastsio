# fastsio (Fast Socket.IO for Python)

[![Build status](https://github.com/miguelgrinberg/python-socketio/workflows/build/badge.svg)](https://github.com/miguelgrinberg/python-socketio/actions) [![codecov](https://codecov.io/gh/miguelgrinberg/python-socketio/branch/main/graph/badge.svg)](https://codecov.io/gh/miguelgrinberg/python-socketio)

**fastsio** is a fork of [python-socketio](https://github.com/miguelgrinberg/python-socketio) with modern Python development improvements inspired by FastAPI.

## Key Features

- **Strong typing** and **FastAPI-like DX** with dependency injection
- **Automatic validation** of data through Pydantic models
- **Lightweight routers** for code organization via `RouterSIO`
- **Flexible middleware system** for authentication, logging, rate limiting, and more
- **Compatibility** with existing ASGI/WSGI stacks
- **Full support** for the Socket.IO protocol

## Installation

```bash
pip install fastsio
```


## Key Differences from python-socketio

| Feature | python-socketio | fastsio |
|---------|-----------------|---------|
| **Type Safety** | Minimal | Strong typing with annotations |
| **Dependency Injection** | ❌ | ✅ FastAPI-style |
| **Pydantic Validation** | ❌ | ✅ Automatic |
| **Routers** | ❌ | ✅ RouterSIO for code organization |
| **Middlewares** | ❌ | ✅ Flexible middleware system |
| **Parameter Annotations** | ❌ | ✅ `SocketID`, `Environ`, `Auth`, etc. |
| **Compatibility** | ✅ | ✅ Full backward compatibility |

## Quick Start

### Simple Server

```python
import fastsio
from fastsio import ASGIApp, SocketID, Environ, Auth, Data

# Create server
sio = fastsio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*"
)

@sio.event
async def connect(
    sid: SocketID, 
    environ: Environ, 
    auth: Auth,
):
    print(f"Client {sid} connected")
    return True

@sio.event
async def disconnect(sid: SocketID):
    print(f"Client {sid} disconnected")

@sio.on("message")
async def handle_message(sid: SocketID, data: Data):
    await sio.emit("response", f"Received: {data}", to=sid)

# ASGI application
app = ASGIApp(sio)
```

### With Type Safety and Dependency Injection

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
    """Connection with automatic dependency injection"""
    print(f"Connection: {sid}, auth: {auth}")
    return True

@router.on("join_room")
async def on_join_room(
    sid: SocketID, 
    server: AsyncServer, 
    data: JoinRoom  # Automatic Pydantic validation
):
    """Join room with data validation"""
    await server.enter_room(sid, data.room)
    await server.emit("joined", {"room": data.room}, to=sid)

@router.on("send_message")
async def on_send_message(
    sid: SocketID, 
    server: AsyncServer, 
    data: Message
):
    """Send message with validation"""
    await server.emit(
        "new_message", 
        data.model_dump(), 
        room=data.room
    )

# Create server and attach router
sio = AsyncServer(async_mode="asgi", cors_allowed_origins="*")
sio.add_router(router)

app = ASGIApp(sio)
```

### FastAPI Integration

```python
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
import fastsio
from fastsio import Environ, Auth, SocketID

app = FastAPI()
sio = fastsio.AsyncServer(async_mode="asgi")

@app.get("/")
async def index():
    return {"message": "FastAPI + fastsio"}

@sio.event
async def connect(sid: SocketID, environ: Environ, auth: Auth):
    await sio.emit("hello", {"message": "Welcome!"}, to=sid)

# Combine FastAPI and Socket.IO
combined_app = fastsio.ASGIApp(sio, app)

if __name__ == "__main__":
    uvicorn.run(combined_app, host="127.0.0.1", port=5000)
```

## Advanced Features

### Middlewares for Cross-Cutting Concerns

```python
from typing import Any
from fastsio import AsyncServer, BaseMiddleware, auth_middleware, logging_middleware

# Custom authentication middleware
class AuthMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__(events=["join_room", "send_message"])
    
    async def before_event(self, event: str, sid: str, data: Any, environ: dict = None, **kwargs):
        if not environ or not environ.get("HTTP_AUTHORIZATION"):
            raise PermissionError("Authentication required")
        return data

# Create server with middlewares
sio = AsyncServer(async_mode="asgi")

# Add middlewares with different scopes
sio.add_middleware(logging_middleware(), global_middleware=True)  # All events
sio.add_middleware(AuthMiddleware())  # Specific events only
sio.add_middleware(
    logging_middleware(), 
    namespace="/admin", 
    events=["admin_action"]
)  # Namespace + event specific

@sio.event
async def join_room(sid: SocketID, data: dict):
    # This will go through AuthMiddleware
    return {"status": "joined"}
```

### Routers for Code Organization

```python
from fastsio import RouterSIO

# Chat router
chat_router = RouterSIO(namespace="/chat")

@chat_router.on("message")
async def chat_message(sid: SocketID, data: Message):
    # Chat logic
    pass

# Admin router
admin_router = RouterSIO(namespace="/admin")

@admin_router.on("user_ban")
async def ban_user(sid: SocketID, data: BanRequest):
    # Ban logic
    pass

# Attach routers
sio.add_router(chat_router)
sio.add_router(admin_router)
```

### Available Types for Injection

```python
from fastsio import (
    SocketID,    # Connection ID
    Environ,     # WSGI/ASGI environ
    Auth,        # Auth data (connect handler only)
    Reason,      # Disconnect reason (disconnect handler only)
    Data,        # Raw event data
    Event,       # Event name
    AsyncServer  # Server instance
)

@router.on("example")
async def example_handler(
    sid: SocketID,
    server: AsyncServer,
    environ: Environ,
    data: Data,
    event: Event
):
    print(f"Event {event} from {sid}: {data}")
```

## Version Compatibility

Compatibility table with JavaScript Socket.IO:

| JavaScript Socket.IO | Socket.IO protocol | Engine.IO protocol | fastsio version |
|---------------------|-------------------|-------------------|-----------------|
| 0.9.x               | 1, 2              | 1, 2              | Not supported   |
| 1.x and 2.x         | 3, 4              | 3                 | Any version     |
| 3.x and 4.x         | 5                 | 4                 | Any version     |

## Documentation

- [Full Documentation](http://fastsio.readthedocs.io/)
- [PyPI](https://pypi.org/project/fastsio/)
- [Changelog](https://github.com/cicwak/fastsio/blob/main/CHANGES.md)

## Contributing

fastsio is based on the excellent work by [Miguel Grinberg](https://github.com/miguelgrinberg) on python-socketio. We've added modern capabilities while maintaining full compatibility with the original API.

## License

MIT License - see [LICENSE](LICENSE) file

---

🌍 **Other Languages**: [Русский](docs/locales/ru/README.ru.md)