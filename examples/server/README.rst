Server Examples
==============

This directory contains various examples of how to use fastsio servers.

Examples
--------

- **aiohttp/**: ASGI server with aiohttp
- **asgi/**: ASGI server with various frameworks
- **sanic/**: Sanic server integration
- **tornado/**: Tornado server integration
- **wsgi/**: WSGI server with various frameworks
- **middleware_example.py**: **NEW!** Complete example demonstrating middleware usage

Middleware Example
-----------------

The `middleware_example.py` file demonstrates the new middleware system in fastsio:

- **Global middlewares**: Applied to all events
- **Event-specific middlewares**: Applied only to specific events
- **Namespace-specific middlewares**: Applied only to specific namespaces
- **Custom middlewares**: Both method-based and call-based implementations
- **Built-in middlewares**: Authentication, logging, and rate limiting

Run the example:

.. code:: bash

    cd examples/server
    python middleware_example.py

Key Features Demonstrated:

1. **Authentication Middleware**: Checks authorization headers
2. **Logging Middleware**: Logs all events and responses
3. **Rate Limiting**: Prevents spam by limiting requests per time window
4. **Data Transformation**: Modifies incoming data and outgoing responses
5. **Custom Middleware Classes**: Shows both sync and async implementations

This example is perfect for understanding how to implement production-ready Socket.IO applications with proper middleware layers.
