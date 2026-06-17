import logging
from unittest import mock

import msgspec
import pytest
from engineio import json, packet as eio_packet
from pydantic import BaseModel

from fastsio import (
    Data,
    Depends,
    RouterSIO,
    SocketID,
    exceptions,
    msgpack_packet,
    namespace,
    packet,
    server,
)
from fastsio.asyncapi import AsyncAPIConfig, AsyncAPIGenerator
from fastsio.dependency import DependencyContext, _resolve_sync_dependencies


@mock.patch(
    "fastsio.server.engineio.Server",
    **{"return_value.generate_id.side_effect": [str(i) for i in range(1, 10)]},
)
class TestServer:
    def teardown_method(self):
        # restore JSON encoder, in case a test changed it
        packet.Packet.json = json

    def test_create(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr, async_handlers=True, foo="bar")
        s.handle_request({}, None)
        s.handle_request({}, None)
        eio.assert_called_once_with(**{"foo": "bar", "async_handlers": False})
        assert s.manager == mgr
        assert s.eio.on.call_count == 3
        assert s.async_handlers
        assert s.packet_class == packet.Packet

    def test_on_event(self, eio):
        s = server.Server()

        @s.on("connect")
        def foo():
            pass

        def bar(reason):
            pass

        s.on("disconnect", bar)
        s.on("disconnect", bar, namespace="/foo")

        assert s.handlers["/"]["connect"] == foo
        assert s.handlers["/"]["disconnect"] == bar
        assert s.handlers["/foo"]["disconnect"] == bar

    def test_event(self, eio):
        s = server.Server()

        @s.event
        def connect():
            pass

        @s.event
        def foo():
            pass

        @s.event()
        def bar():
            pass

        @s.event(namespace="/foo")
        def disconnect():
            pass

        assert s.handlers["/"]["connect"] == connect
        assert s.handlers["/"]["foo"] == foo
        assert s.handlers["/"]["bar"] == bar
        assert s.handlers["/foo"]["disconnect"] == disconnect

    def test_add_nested_router(self, eio):
        s = server.Server()
        parent = RouterSIO(namespace="/api")
        child = RouterSIO(namespace="/chat")

        @child.on("message")
        def message():
            pass

        parent.add_router(child)
        s.add_router(parent)

        assert s.handlers["/api/chat"]["message"] == message

    def test_add_nested_router_with_explicit_namespace(self, eio):
        s = server.Server()
        parent = RouterSIO(namespace="/api")
        child = RouterSIO(namespace="/chat")

        @child.on("connect", namespace="/room")
        def connect():
            pass

        parent.add_router(child)
        s.add_router(parent)

        assert s.handlers["/api/room"]["connect"] == connect

    def test_add_multi_level_nested_router(self, eio):
        s = server.Server()
        root = RouterSIO(namespace="/api")
        parent = RouterSIO(namespace="/v1")
        child = RouterSIO(namespace="/chat")

        @child.on("message")
        def message():
            pass

        parent.add_router(child)
        root.add_router(parent)
        s.add_router(root)

        assert s.handlers["/api/v1/chat"]["message"] == message

    def test_nested_router_add_routers(self, eio):
        s = server.Server()
        parent = RouterSIO(namespace="/api")
        chat = RouterSIO(namespace="/chat")
        admin = RouterSIO(namespace="/admin")

        @chat.on("message")
        def message():
            pass

        @admin.on("stats")
        def stats():
            pass

        parent.add_routers(chat, admin)
        s.add_router(parent)

        assert s.handlers["/api/chat"]["message"] == message
        assert s.handlers["/api/admin"]["stats"] == stats

    def test_nested_router_preserves_handler_metadata(self, eio):
        s = server.Server()
        parent = RouterSIO(namespace="/api")
        child = RouterSIO(namespace="/chat")

        @child.on("message", response_model=str, channel="custom/channel")
        def message():
            pass

        parent.add_router(child)
        s.add_router(parent)

        handler = s.handlers["/api/chat"]["message"]
        assert handler == message
        assert handler._fastsio_response_model is str
        assert handler._fastsio_channel_override == "custom/channel"

    def test_nested_router_prefixes_namespace_handlers(self, eio):
        class MyNamespace(namespace.Namespace):
            pass

        s = server.Server()
        parent = RouterSIO(namespace="/api")
        child = RouterSIO(namespace="/chat")
        ns = MyNamespace(namespace="/room")

        child.register_namespace(ns)
        parent.add_router(child)
        s.add_router(parent)

        assert "/api/room" in s.namespace_handlers
        assert s.namespace_handlers["/api/room"] is not ns
        assert s.namespace_handlers["/api/room"].namespace == "/api/room"
        assert ns.namespace == "/room"

    def test_nested_router_namespace_identity_and_catchall(self, eio):
        s = server.Server()
        parent = RouterSIO(namespace="/api/")
        root_child = RouterSIO(namespace="/")
        catchall_child = RouterSIO(namespace="*")

        @root_child.on("message")
        def message():
            pass

        @catchall_child.on("event")
        def event():
            pass

        parent.add_routers(root_child, catchall_child)
        s.add_router(parent)

        assert s.handlers["/api"]["message"] == message
        assert s.handlers["*"]["event"] == event

    def test_exception_handler(self, eio):
        class UnicornException(Exception):
            def __init__(self, name):
                self.name = name

        s = server.Server()

        @s.on("boom")
        def boom(sid):
            raise UnicornException("spark")

        @s.exception_handler(UnicornException)
        def handle_unicorn(sio: server.Server, sid: SocketID, exc: UnicornException):
            assert sio is s
            return {"sid": sid, "name": exc.name}

        assert s._trigger_event("boom", "/", "1") == {"sid": "1", "name": "spark"}

    def test_router_exception_handler_only_applies_to_router(self, eio):
        class UnicornException(Exception):
            pass

        s = server.Server()
        handled_router = RouterSIO()
        unhandled_router = RouterSIO()

        @handled_router.on("handled")
        def handled(sid):
            raise UnicornException()

        @unhandled_router.on("unhandled")
        def unhandled(sid):
            raise UnicornException()

        @handled_router.exception_handler(UnicornException)
        def handle_unicorn(exc: UnicornException):
            return "router"

        s.add_routers(handled_router, unhandled_router)

        assert s._trigger_event("handled", "/", "1") == "router"
        with pytest.raises(UnicornException):
            s._trigger_event("unhandled", "/", "1")

    def test_router_exception_handlers_are_route_scoped(self, eio):
        class UnicornException(Exception):
            pass

        s = server.Server()
        first_router = RouterSIO(namespace="/first")
        second_router = RouterSIO(namespace="/second")

        def shared_handler(sid):
            raise UnicornException()

        first_router.on("boom", shared_handler)
        second_router.on("boom", shared_handler)

        @first_router.exception_handler(UnicornException)
        def first_handler(exc: UnicornException):
            return "first"

        @second_router.exception_handler(UnicornException)
        def second_handler(exc: UnicornException):
            return "second"

        s.add_routers(first_router, second_router)

        assert s._trigger_event("boom", "/first", "1") == "first"
        assert s._trigger_event("boom", "/second", "1") == "second"

    def test_router_exception_handler_precedes_server_handler(self, eio):
        class UnicornException(Exception):
            pass

        s = server.Server()
        router = RouterSIO()

        @router.on("boom")
        def boom(sid):
            raise UnicornException()

        @s.exception_handler(UnicornException)
        def server_handler(exc: UnicornException):
            return "server"

        @router.exception_handler(Exception)
        def router_handler(exc: Exception):
            return "router"

        s.add_router(router)

        assert s._trigger_event("boom", "/", "1") == "router"

    def test_exception_handler_uses_most_specific_exception_class(self, eio):
        class AppException(Exception):
            pass

        class UnicornException(AppException):
            pass

        s = server.Server()

        @s.on("boom")
        def boom(sid):
            raise UnicornException()

        @s.exception_handler(AppException)
        def app_handler(exc: AppException):
            return "app"

        @s.exception_handler(UnicornException)
        def unicorn_handler(exc: UnicornException):
            return "unicorn"

        assert s._trigger_event("boom", "/", "1") == "unicorn"

    def test_unregistered_exception_is_reraised(self, eio):
        class UnicornException(Exception):
            pass

        s = server.Server()

        @s.on("boom")
        def boom(sid):
            raise UnicornException()

        with pytest.raises(UnicornException):
            s._trigger_event("boom", "/", "1")

    def test_server_exception_handler_catches_namespace_handler(self, eio):
        class UnicornException(Exception):
            pass

        class MyNamespace(namespace.Namespace):
            def on_boom(self, sid):
                raise UnicornException()

        s = server.Server()
        s.register_namespace(MyNamespace("/chat"))

        @s.exception_handler(UnicornException)
        def handle_unicorn(exc: UnicornException):
            return "namespace"

        assert s._trigger_event("boom", "/chat", "1") == "namespace"

    def test_exception_handler_does_not_catch_system_events(self, eio):
        class UnicornException(Exception):
            pass

        s = server.Server()

        @s.exception_handler(UnicornException)
        def handler(exc: UnicornException):
            return "handled"

        @s.on("connect")
        def connect(sid):
            raise UnicornException()

        @s.on("disconnect")
        def disconnect(sid):
            raise UnicornException()

        with pytest.raises(UnicornException):
            s._trigger_event("connect", "/", "1")
        with pytest.raises(UnicornException):
            s._trigger_event("disconnect", "/", "1")

    def test_exception_handler_ack_response(self, eio):
        class UnicornException(Exception):
            pass

        s = server.Server(async_handlers=False)
        s.manager.connect("123", "/")

        @s.on("my message")
        def handler(sid, payload):
            raise UnicornException()

        @s.exception_handler(UnicornException)
        def handle_unicorn(exc: UnicornException):
            return "handled"

        s._handle_eio_message("123", '21000["my message","foo"]')

        s.eio.send.assert_called_once_with("123", '31000["handled"]')

    def test_nested_router_exception_handlers_inherit_and_override(self, eio):
        class UnicornException(Exception):
            pass

        s = server.Server()
        parent = RouterSIO(namespace="/api")
        child = RouterSIO(namespace="/chat")

        @child.on("boom")
        def boom(sid):
            raise UnicornException()

        @parent.exception_handler(UnicornException)
        def parent_handler(exc: UnicornException):
            return "parent"

        parent.add_router(child)
        s.add_router(parent)

        assert s._trigger_event("boom", "/api/chat", "1") == "parent"

        s = server.Server()
        parent = RouterSIO(namespace="/api")
        child = RouterSIO(namespace="/chat")

        @child.on("boom")
        def boom_override(sid):
            raise UnicornException()

        @parent.exception_handler(UnicornException)
        def parent_handler_override(exc: UnicornException):
            return "parent"

        @child.exception_handler(UnicornException)
        def child_handler(exc: UnicornException):
            return "child"

        parent.add_router(child)
        s.add_router(parent)

        assert s._trigger_event("boom", "/api/chat", "1") == "child"

    def test_emit(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.emit(
            "my event",
            {"foo": "bar"},
            to="room",
            skip_sid="123",
            namespace="/foo",
            callback="cb",
        )
        s.manager.emit.assert_called_once_with(
            "my event",
            {"foo": "bar"},
            "/foo",
            room="room",
            skip_sid="123",
            callback="cb",
            ignore_queue=False,
        )
        s.emit(
            "my event",
            {"foo": "bar"},
            room="room",
            skip_sid="123",
            namespace="/foo",
            callback="cb",
            ignore_queue=True,
        )
        s.manager.emit.assert_called_with(
            "my event",
            {"foo": "bar"},
            "/foo",
            room="room",
            skip_sid="123",
            callback="cb",
            ignore_queue=True,
        )

    def test_emit_default_namespace(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.emit(
            "my event",
            {"foo": "bar"},
            to="room",
            skip_sid="123",
            callback="cb",
        )
        s.manager.emit.assert_called_once_with(
            "my event",
            {"foo": "bar"},
            "/",
            room="room",
            skip_sid="123",
            callback="cb",
            ignore_queue=False,
        )
        s.emit(
            "my event",
            {"foo": "bar"},
            room="room",
            skip_sid="123",
            callback="cb",
            ignore_queue=True,
        )
        s.manager.emit.assert_called_with(
            "my event",
            {"foo": "bar"},
            "/",
            room="room",
            skip_sid="123",
            callback="cb",
            ignore_queue=True,
        )

    def test_send(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.send("foo", to="room", skip_sid="123", namespace="/foo", callback="cb")
        s.manager.emit.assert_called_once_with(
            "message",
            "foo",
            "/foo",
            room="room",
            skip_sid="123",
            callback="cb",
            ignore_queue=False,
        )
        s.send(
            "foo",
            room="room",
            skip_sid="123",
            namespace="/foo",
            callback="cb",
            ignore_queue=True,
        )
        s.manager.emit.assert_called_with(
            "message",
            "foo",
            "/foo",
            room="room",
            skip_sid="123",
            callback="cb",
            ignore_queue=True,
        )

    def test_call(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)

        def fake_event_wait(timeout=None):
            assert timeout == 60
            s.manager.emit.call_args_list[0][1]["callback"]("foo", 321)
            return True

        s.eio.create_event.return_value.wait = fake_event_wait
        assert s.call("foo", sid="123") == ("foo", 321)

    def test_call_with_timeout(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)

        def fake_event_wait(timeout=None):
            assert timeout == 12
            return False

        s.eio.create_event.return_value.wait = fake_event_wait
        with pytest.raises(exceptions.TimeoutError):
            s.call("foo", sid="123", timeout=12)

    def test_call_with_broadcast(self, eio):
        s = server.Server()
        with pytest.raises(ValueError):
            s.call("foo")

    def test_call_without_async_handlers(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr, async_handlers=False)
        with pytest.raises(RuntimeError):
            s.call("foo", sid="123", timeout=12)

    def test_enter_room(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.enter_room("123", "room", namespace="/foo")
        s.manager.enter_room.assert_called_once_with("123", "/foo", "room")

    def test_enter_room_default_namespace(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.enter_room("123", "room")
        s.manager.enter_room.assert_called_once_with("123", "/", "room")

    def test_leave_room(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.leave_room("123", "room", namespace="/foo")
        s.manager.leave_room.assert_called_once_with("123", "/foo", "room")

    def test_leave_room_default_namespace(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.leave_room("123", "room")
        s.manager.leave_room.assert_called_once_with("123", "/", "room")

    def test_close_room(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.close_room("room", namespace="/foo")
        s.manager.close_room.assert_called_once_with("room", "/foo")

    def test_close_room_default_namespace(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.close_room("room")
        s.manager.close_room.assert_called_once_with("room", "/")

    def test_rooms(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.rooms("123", namespace="/foo")
        s.manager.get_rooms.assert_called_once_with("123", "/foo")

    def test_rooms_default_namespace(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s.rooms("123")
        s.manager.get_rooms.assert_called_once_with("123", "/")

    def test_handle_request(self, eio):
        s = server.Server()
        s.handle_request("environ", "start_response")
        s.eio.handle_request.assert_called_once_with("environ", "start_response")

    def test_send_packet(self, eio):
        s = server.Server()
        s._send_packet(
            "123",
            packet.Packet(packet.EVENT, ["my event", "my data"], namespace="/foo"),
        )
        s.eio.send.assert_called_once_with("123", '2/foo,["my event","my data"]')

    def test_send_eio_packet(self, eio):
        s = server.Server()
        s._send_eio_packet("123", eio_packet.Packet(eio_packet.MESSAGE, "hello"))
        assert s.eio.send_packet.call_count == 1
        assert s.eio.send_packet.call_args_list[0][0][0] == "123"
        pkt = s.eio.send_packet.call_args_list[0][0][1]
        assert pkt.encode() == "4hello"

    def test_transport(self, eio):
        s = server.Server()
        s.eio.transport = mock.MagicMock(return_value="polling")
        sid_foo = s.manager.connect("123", "/foo")
        assert s.transport(sid_foo, "/foo") == "polling"
        s.eio.transport.assert_called_once_with("123")

    def test_handle_connect(self, eio):
        s = server.Server()
        s.manager.initialize = mock.MagicMock()
        handler = mock.MagicMock()
        s.on("connect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        assert s.manager.is_connected("1", "/")
        handler.assert_called_once_with("1", "environ")
        s.eio.send.assert_called_once_with("123", '0{"sid":"1"}')
        assert s.manager.initialize.call_count == 1
        s._handle_eio_connect("456", "environ")
        assert s.manager.initialize.call_count == 1

    def test_handle_connect_with_auth(self, eio):
        s = server.Server()
        s.manager.initialize = mock.MagicMock()
        handler = mock.MagicMock()
        s.on("connect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", '0{"token":"abc"}')
        assert s.manager.is_connected("1", "/")
        handler.assert_called_with("1", "environ", {"token": "abc"})
        s.eio.send.assert_called_once_with("123", '0{"sid":"1"}')
        assert s.manager.initialize.call_count == 1
        s._handle_eio_connect("456", "environ")
        assert s.manager.initialize.call_count == 1

    def test_handle_connect_with_auth_none(self, eio):
        s = server.Server()
        s.manager.initialize = mock.MagicMock()
        handler = mock.MagicMock(side_effect=[TypeError, None])
        s.on("connect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        assert s.manager.is_connected("1", "/")
        handler.assert_called_with("1", "environ", None)
        s.eio.send.assert_called_once_with("123", '0{"sid":"1"}')
        assert s.manager.initialize.call_count == 1
        s._handle_eio_connect("456", "environ")
        assert s.manager.initialize.call_count == 1

    def test_handle_connect_with_default_implied_namespaces(self, eio):
        s = server.Server()
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        s._handle_eio_message("123", "0/foo,")
        assert s.manager.is_connected("1", "/")
        assert not s.manager.is_connected("2", "/foo")

    def test_handle_connect_with_implied_namespaces(self, eio):
        s = server.Server(namespaces=["/foo"])
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        s._handle_eio_message("123", "0/foo,")
        assert not s.manager.is_connected("1", "/")
        assert s.manager.is_connected("1", "/foo")

    def test_handle_connect_with_all_implied_namespaces(self, eio):
        s = server.Server(namespaces="*")
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        s._handle_eio_message("123", "0/foo,")
        assert s.manager.is_connected("1", "/")
        assert s.manager.is_connected("2", "/foo")

    def test_handle_connect_namespace(self, eio):
        s = server.Server()
        handler = mock.MagicMock()
        s.on("connect", handler, namespace="/foo")
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        assert s.manager.is_connected("1", "/foo")
        handler.assert_called_once_with("1", "environ")
        s.eio.send.assert_called_once_with("123", '0/foo,{"sid":"1"}')

    def test_handle_connect_namespace_twice(self, eio):
        s = server.Server()
        handler = mock.MagicMock()
        s.on("connect", handler, namespace="/foo")
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        s._handle_eio_message("123", "0/foo,")
        assert s.manager.is_connected("1", "/foo")
        handler.assert_called_once_with("1", "environ")
        s.eio.send.assert_any_call("123", '0/foo,{"sid":"1"}')
        s.eio.send.assert_any_call("123", '4/foo,"Unable to connect"')

    def test_handle_connect_always_connect(self, eio):
        s = server.Server(always_connect=True)
        s.manager.initialize = mock.MagicMock()
        handler = mock.MagicMock()
        s.on("connect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        assert s.manager.is_connected("1", "/")
        handler.assert_called_once_with("1", "environ")
        s.eio.send.assert_called_once_with("123", '0{"sid":"1"}')
        assert s.manager.initialize.call_count == 1
        s._handle_eio_connect("456", "environ")
        assert s.manager.initialize.call_count == 1

    def test_handle_connect_rejected(self, eio):
        s = server.Server()
        handler = mock.MagicMock(return_value=False)
        s.on("connect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        assert not s.manager.is_connected("1", "/")
        handler.assert_called_once_with("1", "environ")
        assert not s.manager.is_connected("1", "/")
        s.eio.send.assert_called_once_with(
            "123", '4{"message":"Connection rejected by server"}'
        )
        assert s.environ == {"123": "environ"}

    def test_handle_connect_namespace_rejected(self, eio):
        s = server.Server()
        handler = mock.MagicMock(return_value=False)
        s.on("connect", handler, namespace="/foo")
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        assert not s.manager.is_connected("1", "/foo")
        handler.assert_called_once_with("1", "environ")
        assert not s.manager.is_connected("1", "/foo")
        s.eio.send.assert_called_once_with(
            "123", '4/foo,{"message":"Connection rejected by server"}'
        )
        assert s.environ == {"123": "environ"}

    def test_handle_connect_rejected_always_connect(self, eio):
        s = server.Server(always_connect=True)
        handler = mock.MagicMock(return_value=False)
        s.on("connect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        assert not s.manager.is_connected("1", "/")
        handler.assert_called_once_with("1", "environ")
        s.eio.send.assert_any_call("123", '0{"sid":"1"}')
        s.eio.send.assert_any_call(
            "123", '1{"message":"Connection rejected by server"}'
        )
        assert s.environ == {"123": "environ"}

    def test_handle_connect_namespace_rejected_always_connect(self, eio):
        s = server.Server(always_connect=True)
        handler = mock.MagicMock(return_value=False)
        s.on("connect", handler, namespace="/foo")
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        assert not s.manager.is_connected("1", "/foo")
        handler.assert_called_once_with("1", "environ")
        s.eio.send.assert_any_call("123", '0/foo,{"sid":"1"}')
        s.eio.send.assert_any_call(
            "123", '1/foo,{"message":"Connection rejected by server"}'
        )
        assert s.environ == {"123": "environ"}

    def test_handle_connect_rejected_with_exception(self, eio):
        s = server.Server()
        handler = mock.MagicMock(
            side_effect=exceptions.ConnectionRefusedError("fail_reason")
        )
        s.on("connect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        assert not s.manager.is_connected("1", "/")
        handler.assert_called_once_with("1", "environ")
        s.eio.send.assert_called_once_with("123", '4{"message":"fail_reason"}')
        assert s.environ == {"123": "environ"}

    def test_handle_connect_rejected_with_empty_exception(self, eio):
        s = server.Server()
        handler = mock.MagicMock(side_effect=exceptions.ConnectionRefusedError())
        s.on("connect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        assert not s.manager.is_connected("1", "/")
        handler.assert_called_once_with("1", "environ")
        s.eio.send.assert_called_once_with(
            "123", '4{"message":"Connection rejected by server"}'
        )
        assert s.environ == {"123": "environ"}

    def test_handle_connect_namespace_rejected_with_exception(self, eio):
        s = server.Server()
        handler = mock.MagicMock(
            side_effect=exceptions.ConnectionRefusedError("fail_reason", 1)
        )
        s.on("connect", handler, namespace="/foo")
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        assert not s.manager.is_connected("1", "/foo")
        s.eio.send.assert_called_once_with(
            "123", '4/foo,{"message":"fail_reason","data":1}'
        )
        assert s.environ == {"123": "environ"}

    def test_handle_connect_namespace_rejected_with_empty_exception(self, eio):
        s = server.Server()
        handler = mock.MagicMock(side_effect=exceptions.ConnectionRefusedError())
        s.on("connect", handler, namespace="/foo")
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        assert not s.manager.is_connected("1", "/foo")
        s.eio.send.assert_called_once_with(
            "123", '4/foo,{"message":"Connection rejected by server"}'
        )
        assert s.environ == {"123": "environ"}

    def test_handle_disconnect(self, eio):
        s = server.Server()
        s.manager.disconnect = mock.MagicMock()
        handler = mock.MagicMock()
        s.on("disconnect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        s._handle_eio_disconnect("123", "foo")
        handler.assert_called_once_with("1", "foo")
        s.manager.disconnect.assert_called_once_with("1", "/", ignore_queue=True)
        assert s.environ == {}

    def test_handle_legacy_disconnect(self, eio):
        s = server.Server()
        s.manager.disconnect = mock.MagicMock()
        handler = mock.MagicMock(side_effect=[TypeError, None])
        s.on("disconnect", handler)
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        s._handle_eio_disconnect("123", "foo")
        handler.assert_called_with("1")
        s.manager.disconnect.assert_called_once_with("1", "/", ignore_queue=True)
        assert s.environ == {}

    def test_handle_disconnect_namespace(self, eio):
        s = server.Server()
        handler = mock.MagicMock()
        s.on("disconnect", handler)
        handler_namespace = mock.MagicMock()
        s.on("disconnect", handler_namespace, namespace="/foo")
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        s._handle_eio_disconnect("123", "foo")
        handler.assert_not_called()
        handler_namespace.assert_called_once_with("1", "foo")
        assert s.environ == {}

    def test_handle_disconnect_only_namespace(self, eio):
        s = server.Server()
        handler = mock.MagicMock()
        s.on("disconnect", handler)
        handler_namespace = mock.MagicMock()
        s.on("disconnect", handler_namespace, namespace="/foo")
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        s._handle_eio_message("123", "1/foo,")
        assert handler.call_count == 0
        handler_namespace.assert_called_once_with("1", s.reason.CLIENT_DISCONNECT)
        assert s.environ == {"123": "environ"}

    def test_handle_disconnect_unknown_client(self, eio):
        mgr = mock.MagicMock()
        s = server.Server(client_manager=mgr)
        s._handle_eio_disconnect("123", "foo")

    def test_handle_event(self, eio):
        s = server.Server(async_handlers=False)
        s.manager.connect("123", "/")
        handler = mock.MagicMock()
        catchall_handler = mock.MagicMock()
        s.on("msg", handler)
        s.on("*", catchall_handler)
        s._handle_eio_message("123", '2["msg","a","b"]')
        s._handle_eio_message("123", '2["my message","a","b","c"]')
        handler.assert_called_once_with("1", "a", "b")
        catchall_handler.assert_called_once_with("my message", "1", "a", "b", "c")

    def test_handle_event_with_namespace(self, eio):
        s = server.Server(async_handlers=False)
        s.manager.connect("123", "/foo")
        handler = mock.MagicMock()
        catchall_handler = mock.MagicMock()
        s.on("msg", handler, namespace="/foo")
        s.on("*", catchall_handler, namespace="/foo")
        s._handle_eio_message("123", '2/foo,["msg","a","b"]')
        s._handle_eio_message("123", '2/foo,["my message","a","b","c"]')
        handler.assert_called_once_with("1", "a", "b")
        catchall_handler.assert_called_once_with("my message", "1", "a", "b", "c")

    def test_handle_event_with_catchall_namespace(self, eio):
        s = server.Server(async_handlers=False)
        sid_foo = s.manager.connect("123", "/foo")
        sid_bar = s.manager.connect("123", "/bar")
        sid_baz = s.manager.connect("123", "/baz")
        connect_star_handler = mock.MagicMock()
        msg_foo_handler = mock.MagicMock()
        msg_star_handler = mock.MagicMock()
        star_foo_handler = mock.MagicMock()
        star_star_handler = mock.MagicMock()
        my_message_baz_handler = mock.MagicMock()
        s.on("connect", connect_star_handler, namespace="*")
        s.on("msg", msg_foo_handler, namespace="/foo")
        s.on("msg", msg_star_handler, namespace="*")
        s.on("*", star_foo_handler, namespace="/foo")
        s.on("*", star_star_handler, namespace="*")
        s.on("my message", my_message_baz_handler, namespace="/baz")

        s._trigger_event("connect", "/bar", sid_bar)
        s._handle_eio_message("123", '2/foo,["msg","a","b"]')
        s._handle_eio_message("123", '2/bar,["msg","a","b"]')
        s._handle_eio_message("123", '2/foo,["my message","a","b","c"]')
        s._handle_eio_message("123", '2/bar,["my message","a","b","c"]')
        s._trigger_event("disconnect", "/bar", sid_bar, s.reason.CLIENT_DISCONNECT)
        connect_star_handler.assert_called_once_with("/bar", sid_bar)
        msg_foo_handler.assert_called_once_with(sid_foo, "a", "b")
        msg_star_handler.assert_called_once_with("/bar", sid_bar, "a", "b")
        star_foo_handler.assert_called_once_with("my message", sid_foo, "a", "b", "c")
        star_star_handler.assert_called_once_with(
            "my message", "/bar", sid_bar, "a", "b", "c"
        )

        s._handle_eio_message("123", '2/baz,["my message","a","b","c"]')
        s._handle_eio_message("123", '2/baz,["msg","a","b"]')
        my_message_baz_handler.assert_called_once_with(sid_baz, "a", "b", "c")
        msg_star_handler.assert_called_with("/baz", sid_baz, "a", "b")

    def test_handle_event_with_disconnected_namespace(self, eio):
        s = server.Server(async_handlers=False)
        s.manager.connect("123", "/foo")
        handler = mock.MagicMock()
        s.on("my message", handler, namespace="/bar")
        s._handle_eio_message("123", '2/bar,["my message","a","b","c"]')
        handler.assert_not_called()

    def test_handle_event_binary(self, eio):
        s = server.Server(async_handlers=False)
        s.manager.connect("123", "/")
        handler = mock.MagicMock()
        s.on("my message", handler)
        s._handle_eio_message(
            "123",
            '52-["my message","a",'
            '{"_placeholder":true,"num":1},'
            '{"_placeholder":true,"num":0}]',
        )
        s._handle_eio_message("123", b"foo")
        s._handle_eio_message("123", b"bar")
        handler.assert_called_once_with("1", "a", b"bar", b"foo")

    def test_handle_event_binary_ack(self, eio):
        s = server.Server()
        s.manager.trigger_callback = mock.MagicMock()
        sid = s.manager.connect("123", "/")
        s._handle_eio_message(
            "123", '61-321["my message","a",' '{"_placeholder":true,"num":0}]'
        )
        s._handle_eio_message("123", b"foo")
        s.manager.trigger_callback.assert_called_once_with(
            sid, 321, ["my message", "a", b"foo"]
        )

    def test_handle_event_with_ack(self, eio):
        s = server.Server(async_handlers=False)
        sid = s.manager.connect("123", "/")
        handler = mock.MagicMock(return_value="foo")

        # Use DI for socket ID and data
        def handler_with_di(socket_id: SocketID, data: Data):
            handler(socket_id, data)
            return "foo"

        s.on("my message", handler_with_di)
        s._handle_eio_message("123", '21000["my message","foo"]')
        handler.assert_called_once_with(sid, "foo")
        s.eio.send.assert_called_once_with("123", '31000["foo"]')

    def test_handle_unknown_event_with_ack(self, eio):
        s = server.Server(async_handlers=False)
        s.manager.connect("123", "/")
        handler = mock.MagicMock(return_value="foo")

        # Use DI for socket ID and data
        def handler_with_di(socket_id: SocketID, data: Data):
            handler(socket_id, data)
            return "foo"

        s.on("my message", handler_with_di)
        s._handle_eio_message("123", '21000["another message","foo"]')
        s.eio.send.assert_not_called()

    def test_handle_event_with_ack_none(self, eio):
        s = server.Server(async_handlers=False)
        sid = s.manager.connect("123", "/")
        handler = mock.MagicMock(return_value=None)

        # Use DI for socket ID and data
        def handler_with_di(socket_id: SocketID, data: Data):
            handler(socket_id, data)
            return None

        s.on("my message", handler_with_di)
        s._handle_eio_message("123", '21000["my message","foo"]')
        handler.assert_called_once_with(sid, "foo")
        s.eio.send.assert_called_once_with("123", "31000[]")

    def test_handle_event_with_ack_tuple(self, eio):
        s = server.Server(async_handlers=False)
        handler = mock.MagicMock(return_value=(1, "2", True))

        # Use DI for socket ID and data
        def handler_with_di(socket_id: SocketID, data: Data):
            handler(socket_id, *data)
            return (1, "2", True)

        s.on("my message", handler_with_di)
        sid = s.manager.connect("123", "/")
        s._handle_eio_message("123", '21000["my message","a","b","c"]')
        handler.assert_called_once_with(sid, "a", "b", "c")
        s.eio.send.assert_called_with("123", '31000[1,"2",true]')

    def test_handle_event_with_ack_list(self, eio):
        s = server.Server(async_handlers=False)
        handler = mock.MagicMock(return_value=[1, "2", True])

        # Use DI for socket ID and data
        def handler_with_di(socket_id: SocketID, data: Data):
            handler(socket_id, *data)
            return [1, "2", True]

        s.on("my message", handler_with_di)
        sid = s.manager.connect("123", "/")
        s._handle_eio_message("123", '21000["my message","a","b","c"]')
        handler.assert_called_once_with(sid, "a", "b", "c")
        s.eio.send.assert_called_with("123", '31000[[1,"2",true]]')

    def test_handle_event_with_ack_binary(self, eio):
        s = server.Server(async_handlers=False)
        handler = mock.MagicMock(return_value=b"foo")

        # Use DI for socket ID and data
        def handler_with_di(socket_id: SocketID, data: Data):
            handler(socket_id, data)
            return b"foo"

        s.on("my message", handler_with_di)
        sid = s.manager.connect("123", "/")
        s._handle_eio_message("123", '21000["my message","foo"]')
        handler.assert_any_call(sid, "foo")

    def test_handle_error_packet(self, eio):
        s = server.Server()
        with pytest.raises(ValueError):
            s._handle_eio_message("123", "4")

    def test_handle_invalid_packet(self, eio):
        s = server.Server()
        with pytest.raises(ValueError):
            s._handle_eio_message("123", "9")

    def test_send_with_ack(self, eio):
        s = server.Server()
        s.handlers["/"] = {}
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        cb = mock.MagicMock()
        id1 = s.manager._generate_ack_id("1", cb)
        id2 = s.manager._generate_ack_id("1", cb)
        s._send_packet("123", packet.Packet(packet.EVENT, ["my event", "foo"], id=id1))
        s._send_packet("123", packet.Packet(packet.EVENT, ["my event", "bar"], id=id2))
        s._handle_eio_message("123", '31["foo",2]')
        cb.assert_called_once_with("foo", 2)

    def test_send_with_ack_namespace(self, eio):
        s = server.Server()
        s.handlers["/foo"] = {}
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        cb = mock.MagicMock()
        id = s.manager._generate_ack_id("1", cb)
        s._send_packet(
            "123",
            packet.Packet(packet.EVENT, ["my event", "foo"], namespace="/foo", id=id),
        )
        s._handle_eio_message("123", '3/foo,1["foo",2]')
        cb.assert_called_once_with("foo", 2)

    def test_session(self, eio):
        fake_session = {}

        def fake_get_session(eio_sid):
            assert eio_sid == "123"
            return fake_session

        def fake_save_session(eio_sid, session):
            global fake_session
            assert eio_sid == "123"
            fake_session = session

        s = server.Server()
        s.handlers["/"] = {}
        s.handlers["/ns"] = {}
        s.eio.get_session = fake_get_session
        s.eio.save_session = fake_save_session
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        s._handle_eio_message("123", "0/ns")
        sid = s.manager.sid_from_eio_sid("123", "/")
        sid2 = s.manager.sid_from_eio_sid("123", "/ns")
        s.save_session(sid, {"foo": "bar"})
        with s.session(sid) as session:
            assert session == {"foo": "bar"}
            session["foo"] = "baz"
            session["bar"] = "foo"
        assert s.get_session(sid) == {"foo": "baz", "bar": "foo"}
        assert fake_session == {"/": {"foo": "baz", "bar": "foo"}}
        with s.session(sid2, namespace="/ns") as session:
            assert session == {}
            session["a"] = "b"
        assert s.get_session(sid2, namespace="/ns") == {"a": "b"}
        assert fake_session == {
            "/": {"foo": "baz", "bar": "foo"},
            "/ns": {"a": "b"},
        }

    def test_disconnect(self, eio):
        s = server.Server()
        s.handlers["/"] = {}
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        s.disconnect("1")
        s.eio.send.assert_any_call("123", "1")

    def test_disconnect_ignore_queue(self, eio):
        s = server.Server()
        s.handlers["/"] = {}
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        s.disconnect("1", ignore_queue=True)
        s.eio.send.assert_any_call("123", "1")

    def test_disconnect_namespace(self, eio):
        s = server.Server()
        s.handlers["/foo"] = {}
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        s.disconnect("1", namespace="/foo")
        s.eio.send.assert_any_call("123", "1/foo,")

    def test_disconnect_twice(self, eio):
        s = server.Server()
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        s.disconnect("1")
        calls = s.eio.send.call_count
        s.disconnect("1")
        assert calls == s.eio.send.call_count

    def test_disconnect_twice_namespace(self, eio):
        s = server.Server()
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        s.disconnect("123", namespace="/foo")
        calls = s.eio.send.call_count
        s.disconnect("123", namespace="/foo")
        assert calls == s.eio.send.call_count

    def test_namespace_handler(self, eio):
        result = {}

        class MyNamespace(namespace.Namespace):
            def on_connect(self, sid, environ):
                result["result"] = (sid, environ)

            def on_disconnect(self, sid, reason):
                result["result"] = ("disconnect", sid, reason)

            def on_foo(self, sid, data):
                result["result"] = (sid, data)

            def on_bar(self, sid):
                result["result"] = "bar"

            def on_baz(self, sid, data1, data2):
                result["result"] = (data1, data2)

        s = server.Server(async_handlers=False)
        s.register_namespace(MyNamespace("/foo"))
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        assert result["result"] == ("1", "environ")
        s._handle_eio_message("123", '2/foo,["foo","a"]')
        assert result["result"] == ("1", "a")
        s._handle_eio_message("123", '2/foo,["bar"]')
        assert result["result"] == "bar"
        s._handle_eio_message("123", '2/foo,["baz","a","b"]')
        assert result["result"] == ("a", "b")
        s.disconnect("1", "/foo")
        assert result["result"] == ("disconnect", "1", s.reason.SERVER_DISCONNECT)

    def test_catchall_namespace_handler(self, eio):
        result = {}

        class MyNamespace(namespace.Namespace):
            def on_connect(self, ns, sid, environ):
                result["result"] = (sid, ns, environ)

            def on_disconnect(self, ns, sid):
                result["result"] = ("disconnect", sid, ns)

            def on_foo(self, ns, sid, data):
                result["result"] = (sid, ns, data)

            def on_bar(self, ns, sid):
                result["result"] = "bar" + ns

            def on_baz(self, ns, sid, data1, data2):
                result["result"] = (ns, data1, data2)

        s = server.Server(async_handlers=False, namespaces="*")
        s.register_namespace(MyNamespace("*"))
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0/foo,")
        assert result["result"] == ("1", "/foo", "environ")
        s._handle_eio_message("123", '2/foo,["foo","a"]')
        assert result["result"] == ("1", "/foo", "a")
        s._handle_eio_message("123", '2/foo,["bar"]')
        assert result["result"] == "bar/foo"
        s._handle_eio_message("123", '2/foo,["baz","a","b"]')
        assert result["result"] == ("/foo", "a", "b")
        s.disconnect("1", "/foo")
        assert result["result"] == ("disconnect", "1", "/foo")

    def test_bad_namespace_handler(self, eio):
        class Dummy:
            pass

        class AsyncNS(namespace.Namespace):
            def is_asyncio_based(self):
                return True

        s = server.Server()
        with pytest.raises(ValueError):
            s.register_namespace(123)
        with pytest.raises(ValueError):
            s.register_namespace(Dummy)
        with pytest.raises(ValueError):
            s.register_namespace(Dummy())
        with pytest.raises(ValueError):
            s.register_namespace(namespace.Namespace)
        with pytest.raises(ValueError):
            s.register_namespace(AsyncNS())

    def test_get_environ(self, eio):
        s = server.Server()
        s.handlers["/"] = {}
        s._handle_eio_connect("123", "environ")
        s._handle_eio_message("123", "0")
        sid = s.manager.sid_from_eio_sid("123", "/")
        assert s.get_environ(sid) == "environ"
        assert s.get_environ("foo") is None

    def test_logger(self, eio):
        s = server.Server(logger=False)
        assert s.logger.getEffectiveLevel() == logging.ERROR
        s.logger.setLevel(logging.NOTSET)
        s = server.Server(logger=True)
        assert s.logger.getEffectiveLevel() == logging.INFO
        s.logger.setLevel(logging.WARNING)
        s = server.Server(logger=True)
        assert s.logger.getEffectiveLevel() == logging.WARNING
        s.logger.setLevel(logging.NOTSET)
        s = server.Server(logger="foo")
        assert s.logger == "foo"

    def test_engineio_logger(self, eio):
        server.Server(engineio_logger="foo")
        eio.assert_called_once_with(**{"logger": "foo", "async_handlers": False})

    def test_msgpack(self, eio):
        s = server.Server(serializer="msgpack")
        assert s.packet_class == msgpack_packet.MsgPackPacket

    def test_msgpack_pydantic_payload_injection(self, eio):
        class User(BaseModel):
            name: str
            groups: set[str] = set()
            email: str | None = None

        s = server.Server(serializer="msgpack", async_handlers=False)
        s.manager.connect("123", "/")
        handler = mock.MagicMock()

        @s.on("user")
        def on_user(user: User):
            handler(user)

        pkt = msgpack_packet.MsgPackPacket(
            packet.EVENT,
            data=["user", {"name": "alice", "groups": ["admin"]}],
        )
        s._handle_eio_message("123", pkt.encode())

        handler.assert_called_once()
        user = handler.call_args.args[0]
        assert isinstance(user, User)
        assert user.name == "alice"
        assert user.groups == {"admin"}
        assert user.email is None

    def test_msgpack_msgspec_payload_injection(self, eio):
        class User(msgspec.Struct):
            name: str
            groups: set[str] = set()
            email: str | None = None

        s = server.Server(serializer="msgpack", async_handlers=False)
        s.manager.connect("123", "/")
        handler = mock.MagicMock()

        @s.on("user")
        def on_user(user: User):
            handler(user)

        pkt = msgpack_packet.MsgPackPacket(
            packet.EVENT,
            data=["user", {"name": "alice", "groups": ["admin"]}],
        )
        s._handle_eio_message("123", pkt.encode())

        handler.assert_called_once_with(
            User(name="alice", groups={"admin"}, email=None)
        )

    def test_msgpack_invalid_msgspec_payload(self, eio):
        class User(msgspec.Struct):
            name: str
            groups: set[str] = set()

        s = server.Server(serializer="msgpack", async_handlers=False)
        s.manager.connect("123", "/")
        handler = mock.MagicMock()

        @s.on("user")
        def on_user(user: User):
            handler(user)

        pkt = msgpack_packet.MsgPackPacket(
            packet.EVENT,
            data=["user", {"name": 123, "groups": ["admin"]}],
        )
        with pytest.raises(ValueError, match="Failed to validate payload for 'User'"):
            s._handle_eio_message("123", pkt.encode())
        handler.assert_not_called()

    def test_sync_dependency_msgspec_struct(self, eio):
        class User(msgspec.Struct):
            name: str
            groups: set[str] = set()

        def handler(user: User):
            return user

        with DependencyContext(data={"name": "alice", "groups": ["admin"]}):
            resolved = _resolve_sync_dependencies(handler)

        assert resolved == {"user": User(name="alice", groups={"admin"})}

    def test_msgspec_response_model(self, eio):
        class Reply(msgspec.Struct):
            ok: bool
            count: int

        s = server.Server()

        @s.on("reply", response_model=Reply)
        def reply():
            return {"ok": True, "count": 2}

        assert s._validate_response(reply, {"ok": True, "count": 2}) == {
            "ok": True,
            "count": 2,
        }

    def test_msgspec_response_model_dict(self, eio):
        class Reply(msgspec.Struct):
            ok: bool
            count: int

        s = server.Server()

        @s.on("reply", response_model={"reply.done": Reply})
        def reply():
            return "reply.done", {"ok": True, "count": 2}, "extra"

        assert s._validate_response(
            reply, ("reply.done", {"ok": True, "count": 2}, "extra")
        ) == ("reply.done", {"ok": True, "count": 2}, "extra")

    def test_msgpack_msgspec_response_model_ack_serializes(self, eio):
        class Reply(msgspec.Struct):
            ok: bool
            count: int

        s = server.Server(serializer="msgpack", async_handlers=False)
        s.manager.connect("123", "/")

        @s.on("reply", response_model=Reply)
        def reply(sid):
            return {"ok": True, "count": 2}

        pkt = msgpack_packet.MsgPackPacket(packet.EVENT, id=7, data=["reply"])
        s._handle_eio_message("123", pkt.encode())

        encoded_ack = s.eio.send.call_args.args[1]
        ack = msgpack_packet.MsgPackPacket(encoded_packet=encoded_ack)
        assert ack.packet_type == packet.ACK
        assert ack.id == 7
        assert ack.data == [{"ok": True, "count": 2}]

    def test_pydantic_response_model_ack_serializes(self, eio):
        class Reply(BaseModel):
            ok: bool
            tags: set[str] = set()

        s = server.Server(async_handlers=False)
        s.manager.connect("123", "/")

        @s.on("reply", response_model=Reply)
        def reply(sid):
            return {"ok": True, "tags": {"fast"}}

        pkt = packet.Packet(packet.EVENT, id=7, data=["reply"])
        s._handle_eio_message("123", pkt.encode())

        encoded_ack = s.eio.send.call_args.args[1]
        ack = packet.Packet(encoded_packet=encoded_ack)
        assert ack.packet_type == packet.ACK
        assert ack.id == 7
        assert ack.data == [{"ok": True, "tags": ["fast"]}]

    def test_asyncapi_msgspec_payload_schema(self, eio):
        class User(msgspec.Struct):
            name: str
            groups: set[str] = set()

        s = server.Server()

        @s.on("user")
        def on_user(user: User):
            return None

        doc = AsyncAPIGenerator(AsyncAPIConfig()).generate(s)

        payload = doc["channels"]["user"]["messages"]["userRequest"]["payload"]
        assert payload == {"$ref": "#/components/schemas/User"}
        assert doc["components"]["schemas"]["User"]["title"] == "User"
        assert (
            doc["components"]["schemas"]["User"]["properties"]["groups"]["default"]
            == []
        )

    def test_custom_serializer(self, eio):
        class CustomPacket(packet.Packet):
            pass

        s = server.Server(serializer=CustomPacket)
        assert s.packet_class == CustomPacket

    def test_custom_json(self, eio):
        # Warning: this test cannot run in parallel with other tests, as it
        # changes the JSON encoding/decoding functions

        class CustomJSON:
            @staticmethod
            def dumps(*args, **kwargs):
                return "*** encoded ***"

            @staticmethod
            def loads(*args, **kwargs):
                return "+++ decoded +++"

        server.Server(json=CustomJSON)
        eio.assert_called_once_with(**{"json": CustomJSON, "async_handlers": False})

        pkt = packet.Packet(
            packet_type=packet.EVENT,
            data={"foo": "bar"},
        )
        assert pkt.encode() == "2*** encoded ***"
        pkt2 = packet.Packet(encoded_packet=pkt.encode())
        assert pkt2.data == "+++ decoded +++"

        # restore the default JSON module
        packet.Packet.json = json

    def test_async_handlers(self, eio):
        s = server.Server(async_handlers=True)
        sid = s.manager.connect("123", "/")
        s._handle_eio_message("123", '2["my message","a","b","c"]')
        s.eio.start_background_task.assert_called_once_with(
            s._handle_event_internal,
            s,
            sid,
            "123",
            ["my message", "a", "b", "c"],
            "/",
            None,
        )

    def test_shutdown(self, eio):
        s = server.Server()
        s.shutdown()
        s.eio.shutdown.assert_called_once_with()

    def test_start_background_task(self, eio):
        s = server.Server()
        s.start_background_task("foo", "bar", baz="baz")
        s.eio.start_background_task.assert_called_once_with("foo", "bar", baz="baz")

    def test_sleep(self, eio):
        s = server.Server()
        s.sleep(1.23)
        s.eio.sleep.assert_called_once_with(1.23)
