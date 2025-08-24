"""
Tests for the middleware system.
"""

import pytest
from unittest.mock import Mock, MagicMock

from src.fastsio.middlewares import (
    BaseMiddleware,
    SyncMiddleware,
    MiddlewareChain,
    auth_middleware,
    logging_middleware,
    rate_limit_middleware,
)


class TestBaseMiddleware:
    """Test BaseMiddleware class."""
    
    def test_init_defaults(self):
        """Test middleware initialization with defaults."""
        middleware = BaseMiddleware()
        assert middleware.events == set()
        assert middleware.namespace is None
        assert middleware.global_middleware is True
    
    def test_init_with_events_string(self):
        """Test middleware initialization with single event."""
        middleware = BaseMiddleware(events="test_event")
        assert middleware.events == {"test_event"}
        assert middleware.global_middleware is False
        assert middleware.namespace is None
    
    def test_init_with_events_list(self):
        """Test middleware initialization with event list."""
        middleware = BaseMiddleware(events=["event1", "event2"])
        assert middleware.events == {"event1", "event2"}
        assert middleware.global_middleware is False
        assert middleware.namespace is None
    
    def test_init_with_namespace(self):
        """Test middleware initialization with namespace."""
        middleware = BaseMiddleware(namespace="/test")
        assert middleware.namespace == "/test"
        assert middleware.global_middleware is False
        assert middleware.events == set()
    
    def test_init_with_global(self):
        """Test middleware initialization with global flag."""
        middleware = BaseMiddleware(global_middleware=True)
        assert middleware.global_middleware is True
        assert middleware.events == set()
        assert middleware.namespace is None
    
    def test_should_run_global(self):
        """Test should_run for global middleware."""
        middleware = BaseMiddleware(global_middleware=True)
        assert middleware.should_run("any_event", "/any_namespace") is True
    
    def test_should_run_namespace_filter(self):
        """Test should_run with namespace filter."""
        middleware = BaseMiddleware(namespace="/test")
        assert middleware.should_run("event", "/test") is True
        assert middleware.should_run("event", "/other") is False
    
    def test_should_run_event_filter(self):
        """Test should_run with event filter."""
        middleware = BaseMiddleware(events=["event1", "event2"])
        assert middleware.should_run("event1", "/test") is True
        assert middleware.should_run("event2", "/test") is True
        assert middleware.should_run("event3", "/test") is False
    
    def test_should_run_combined_filters(self):
        """Test should_run with combined filters."""
        middleware = BaseMiddleware(events=["event1"], namespace="/test")
        assert middleware.should_run("event1", "/test") is True
        assert middleware.should_run("event1", "/other") is False
        assert middleware.should_run("event2", "/test") is False
    
    @pytest.mark.asyncio
    async def test_before_event_default(self):
        """Test default before_event implementation."""
        middleware = BaseMiddleware()
        data = {"test": "data"}
        result = await middleware.before_event("test_event", "test_sid", data)
        assert result == data
    
    @pytest.mark.asyncio
    async def test_after_event_default(self):
        """Test default after_event implementation."""
        middleware = BaseMiddleware()
        response = {"status": "ok"}
        result = await middleware.after_event("test_event", "test_sid", response)
        assert result == response
    
    @pytest.mark.asyncio
    async def test_call_default_implementation(self):
        """Test default __call__ implementation."""
        middleware = BaseMiddleware()
        handler = Mock(return_value="response")
        
        result = await middleware.__call__(
            "test_event", "test_sid", "test_data", handler
        )
        
        assert result == "response"
        handler.assert_called_once_with("test_data")
    
    @pytest.mark.asyncio
    async def test_handle_exception_default(self):
        """Test default handle_exception implementation."""
        middleware = BaseMiddleware()
        exc = Exception("test error")
        
        with pytest.raises(Exception, match="test error"):
            await middleware.handle_exception(exc, "test_event", "test_sid", "test_data")


class TestSyncMiddleware:
    """Test SyncMiddleware class."""
    
    def test_before_event_sync(self):
        """Test sync before_event implementation."""
        middleware = SyncMiddleware()
        data = {"test": "data"}
        result = middleware.before_event("test_event", "test_sid", data)
        assert result == data
    
    def test_after_event_sync(self):
        """Test sync after_event implementation."""
        middleware = SyncMiddleware()
        response = {"status": "ok"}
        result = middleware.after_event("test_event", "test_sid", response)
        assert result == response
    
    def test_call_sync_implementation(self):
        """Test sync __call__ implementation."""
        middleware = SyncMiddleware()
        handler = Mock(return_value="response")
        
        result = middleware.__call__(
            "test_event", "test_sid", "test_data", handler
        )
        
        assert result == "response"
        handler.assert_called_once_with("test_data")
    
    def test_handle_exception_sync(self):
        """Test sync handle_exception implementation."""
        middleware = SyncMiddleware()
        exc = Exception("test error")
        
        with pytest.raises(Exception, match="test error"):
            middleware.handle_exception(exc, "test_event", "test_sid", "test_data")


class TestMiddlewareChain:
    """Test MiddlewareChain class."""
    
    def test_init(self):
        """Test chain initialization."""
        chain = MiddlewareChain()
        assert chain.middlewares == []
    
    def test_add_middleware(self):
        """Test adding middleware to chain."""
        chain = MiddlewareChain()
        middleware = BaseMiddleware()
        
        chain.add_middleware(middleware)
        assert middleware in chain.middlewares
    
    def test_remove_middleware(self):
        """Test removing middleware from chain."""
        chain = MiddlewareChain()
        middleware = BaseMiddleware()
        
        chain.add_middleware(middleware)
        assert middleware in chain.middlewares
        
        chain.remove_middleware(middleware)
        assert middleware not in chain.middlewares
    
    def test_remove_nonexistent_middleware(self):
        """Test removing non-existent middleware."""
        chain = MiddlewareChain()
        middleware = BaseMiddleware()
        
        # Should not raise error
        chain.remove_middleware(middleware)
        assert chain.middlewares == []
    
    @pytest.mark.asyncio
    async def test_execute_no_middlewares(self):
        """Test executing chain with no middlewares."""
        chain = MiddlewareChain()
        handler = Mock(return_value="response")
        
        result = await chain.execute(
            "test_event", "test_sid", "test_data", handler
        )
        
        assert result == "response"
        handler.assert_called_once_with("test_data")
    
    @pytest.mark.asyncio
    async def test_execute_with_middleware(self):
        """Test executing chain with middleware."""
        chain = MiddlewareChain()
        
        # Create middleware that modifies data
        class TestMiddleware(BaseMiddleware):
            async def before_event(self, event, sid, data, namespace=None, environ=None, auth=None, server=None, **kwargs):
                return f"modified_{data}"
            
            async def after_event(self, event, sid, response, namespace=None, environ=None, auth=None, server=None, **kwargs):
                return f"modified_{response}"
        
        middleware = TestMiddleware()
        chain.add_middleware(middleware)
        
        handler = Mock(return_value="response")
        
        result = await chain.execute(
            "test_event", "test_sid", "test_data", handler
        )
        
        assert result == "modified_response"
        handler.assert_called_once_with("test_sid", "modified_test_data")
    
    @pytest.mark.asyncio
    async def test_execute_middleware_filtering(self):
        """Test middleware filtering in chain execution."""
        chain = MiddlewareChain()
        
        # Create middleware that only runs for specific event
        class FilteredMiddleware(BaseMiddleware):
            async def before_event(self, event, sid, data, namespace=None, environ=None, auth=None, server=None, **kwargs):
                return f"filtered_{data}"
        
        middleware = FilteredMiddleware(events=["event1"])
        chain.add_middleware(middleware)
        
        handler = Mock(return_value="response")
        
        # Should not run middleware for filtered event
        result = await chain.execute(
            "event2", "test_sid", "test_data", handler
        )
        
        assert result == "response"
        handler.assert_called_once_with("test_sid", "test_data")
        
        # Reset mock for next call
        handler.reset_mock()
        
        # Should run middleware for allowed event
        result = await chain.execute(
            "event1", "test_sid", "test_data", handler
        )
        
        assert result == "response"
        handler.assert_called_once_with("test_sid", "filtered_test_data")


class TestConvenienceMiddlewares:
    """Test convenience middleware functions."""
    
    def test_auth_middleware(self):
        """Test auth_middleware function."""
        def auth_checker(sid, environ):
            return environ.get("HTTP_AUTHORIZATION") == "Bearer token"
        
        middleware = auth_middleware(auth_checker)
        assert isinstance(middleware, BaseMiddleware)
        assert middleware.events == set()  # Applies to all events
    
    def test_logging_middleware(self):
        """Test logging_middleware function."""
        middleware = logging_middleware()
        assert isinstance(middleware, BaseMiddleware)
        assert middleware.events == set()  # Applies to all events
    
    def test_rate_limit_middleware(self):
        """Test rate_limit_middleware function."""
        middleware = rate_limit_middleware(max_requests=5, window_seconds=60)
        assert isinstance(middleware, BaseMiddleware)
        assert middleware.events == set()  # Applies to all events


class TestMiddlewareIntegration:
    """Test middleware integration with server."""
    
    @pytest.mark.asyncio
    async def test_middleware_with_sync_handler(self):
        """Test middleware with synchronous handler."""
        from fastsio import Server
        
        server = Server()
        
        # Add middleware that modifies data
        class DataModifierMiddleware(BaseMiddleware):
            async def before_event(self, event, sid, data, namespace=None, environ=None, auth=None, server=None, **kwargs):
                if isinstance(data, dict):
                    data["modified"] = True
                return data
        
        server.add_middleware(DataModifierMiddleware())
        
        # Register handler
        @server.event
        def test_event(sid, data):
            return {"received": data, "handler_called": True}
        
        # Test middleware execution
        result = server._trigger_event("test_event", "/", "test_sid", {"test": "data"})
        
        # For sync handlers, we need to await the result since middleware chain is async
        if hasattr(result, '__await__'):
            result = await result
        
        assert result["received"]["modified"] is True
        assert result["handler_called"] is True
    
    @pytest.mark.asyncio
    async def test_middleware_with_async_handler(self):
        """Test middleware with asynchronous handler."""
        from fastsio import AsyncServer
        
        server = AsyncServer()
        
        # Add middleware that modifies data
        class DataModifierMiddleware(BaseMiddleware):
            async def before_event(self, event, sid, data, namespace=None, environ=None, auth=None, server=None, **kwargs):
                if isinstance(data, dict):
                    data["modified"] = True
                return data
        
        server.add_middleware(DataModifierMiddleware())
        
        # Register handler
        @server.event
        async def test_event(sid, data):
            return {"received": data, "handler_called": True}
        
        # Test middleware execution
        result = await server._trigger_event("test_event", "/", "test_sid", {"test": "data"})
        
        assert result["received"]["modified"] is True
        assert result["handler_called"] is True
