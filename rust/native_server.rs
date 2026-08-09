use std::{
    collections::HashMap,
    future::Future,
    pin::Pin,
    sync::{Arc, RwLock},
};

use pyo3::{exceptions::PyRuntimeError, prelude::*, types::PyAny};
use pyo3_async_runtimes::TaskLocals;
use socketioxide::{
    SocketIo,
    adapter::Adapter,
    extract::{AckSender, Data, SocketRef, TryData},
    layer::SocketIoLayer,
    socket::DisconnectReason,
};
use socketioxide_core::adapter::Spawnable;
use socketioxide_redis::{RedisAdapter, RedisAdapterCtr, drivers::redis::redis_client as redis};

use crate::wire_value::WireValue;

type PythonFuture = Pin<Box<dyn Future<Output = PyResult<Py<PyAny>>> + Send>>;
type IoFuture = Pin<Box<dyn Future<Output = PyResult<()>> + Send>>;
type EventHandlers = HashMap<String, Arc<Py<PyAny>>>;
type NamespaceHandlers = HashMap<String, EventHandlers>;

trait IoOperations: Send + Sync {
    fn enter_room(&self, sid: &str, room: String) -> PyResult<()>;
    fn leave_room(&self, sid: &str, room: String) -> PyResult<()>;
    fn rooms(&self, sid: &str) -> PyResult<Vec<String>>;
    fn disconnect(&self, sid: &str) -> PyResult<()>;
    fn emit(&self, event: String, data: WireValue, to: Option<String>) -> IoFuture;
}

impl<A: Adapter> IoOperations for SocketIo<A> {
    fn enter_room(&self, sid: &str, room: String) -> PyResult<()> {
        let socket = self
            .sockets()
            .into_iter()
            .find(|socket| socket.id.to_string() == sid)
            .ok_or_else(|| PyRuntimeError::new_err(format!("unknown socket id: {sid}")))?;
        socket.join(room);
        Ok(())
    }

    fn leave_room(&self, sid: &str, room: String) -> PyResult<()> {
        let socket = self
            .sockets()
            .into_iter()
            .find(|socket| socket.id.to_string() == sid)
            .ok_or_else(|| PyRuntimeError::new_err(format!("unknown socket id: {sid}")))?;
        socket.leave(room);
        Ok(())
    }

    fn rooms(&self, sid: &str) -> PyResult<Vec<String>> {
        let socket = self
            .sockets()
            .into_iter()
            .find(|socket| socket.id.to_string() == sid)
            .ok_or_else(|| PyRuntimeError::new_err(format!("unknown socket id: {sid}")))?;
        Ok(socket
            .rooms()
            .into_iter()
            .map(|room| room.into_owned())
            .collect())
    }

    fn disconnect(&self, sid: &str) -> PyResult<()> {
        let socket = self
            .sockets()
            .into_iter()
            .find(|socket| socket.id.to_string() == sid)
            .ok_or_else(|| PyRuntimeError::new_err(format!("unknown socket id: {sid}")))?;
        socket
            .disconnect()
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))
    }

    fn emit(&self, event: String, data: WireValue, to: Option<String>) -> IoFuture {
        let io = self.clone();
        Box::pin(async move {
            if let Some(target) = to {
                if let Some(socket) = io
                    .sockets()
                    .into_iter()
                    .find(|socket| socket.id.to_string() == target)
                {
                    socket
                        .emit(event, &data)
                        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                } else {
                    io.to(target)
                        .emit(event, &data)
                        .await
                        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                }
            } else {
                io.emit(event, &data)
                    .await
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
            }
            Ok(())
        })
    }
}

enum PythonCall {
    Ready(Py<PyAny>),
    Awaitable(PythonFuture),
}

async fn invoke_handler(
    callback: Arc<Py<PyAny>>,
    locals: TaskLocals,
    sid: String,
    data: WireValue,
) -> PyResult<WireValue> {
    let call = Python::attach(|py| -> PyResult<PythonCall> {
        let data = data.into_python(py)?;
        let result = callback.call1(py, (sid, data))?;
        let inspect = py.import("inspect")?;
        let is_awaitable = inspect
            .call_method1("isawaitable", (result.bind(py),))?
            .is_truthy()?;
        if is_awaitable {
            let future =
                pyo3_async_runtimes::into_future_with_locals(&locals, result.into_bound(py))?;
            Ok(PythonCall::Awaitable(Box::pin(future)))
        } else {
            Ok(PythonCall::Ready(result))
        }
    })?;

    let result = match call {
        PythonCall::Ready(result) => result,
        PythonCall::Awaitable(future) => future.await?,
    };
    Python::attach(|py| WireValue::from_python(result.bind(py)))
}

#[pyclass(module = "fastsio._rust")]
pub struct NativeServer {
    handlers: NamespaceHandlers,
    io: Arc<RwLock<Option<Arc<dyn IoOperations>>>>,
    redis_url: Option<String>,
}

#[pymethods]
impl NativeServer {
    #[new]
    #[pyo3(signature = (redis_url=None))]
    fn new(redis_url: Option<String>) -> Self {
        Self {
            handlers: HashMap::new(),
            io: Arc::new(RwLock::new(None)),
            redis_url,
        }
    }

    #[pyo3(signature = (event, handler, namespace="/"))]
    fn on(&mut self, event: String, handler: Py<PyAny>, namespace: &str) {
        self.handlers
            .entry(namespace.to_owned())
            .or_default()
            .insert(event, Arc::new(handler));
    }

    fn enter_room(&self, sid: &str, room: String) -> PyResult<()> {
        self.running_io()?.enter_room(sid, room)
    }

    fn leave_room(&self, sid: &str, room: String) -> PyResult<()> {
        self.running_io()?.leave_room(sid, room)
    }

    fn rooms(&self, sid: &str) -> PyResult<Vec<String>> {
        self.running_io()?.rooms(sid)
    }

    fn disconnect(&self, sid: &str) -> PyResult<()> {
        self.running_io()?.disconnect(sid)
    }

    #[pyo3(signature = (event, data, to=None))]
    fn emit<'py>(
        &self,
        py: Python<'py>,
        event: String,
        data: &Bound<'_, PyAny>,
        to: Option<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let data = WireValue::from_python(data)?;
        let io = self
            .io
            .read()
            .map_err(|_| PyRuntimeError::new_err("native server state is poisoned"))?
            .clone()
            .ok_or_else(|| PyRuntimeError::new_err("native server is not running"))?;
        pyo3_async_runtimes::tokio::future_into_py(
            py,
            async move { io.emit(event, data, to).await },
        )
    }

    #[pyo3(signature = (host="127.0.0.1", port=8765))]
    fn serve<'py>(&self, py: Python<'py>, host: &str, port: u16) -> PyResult<Bound<'py, PyAny>> {
        let handlers = Arc::new(self.handlers.clone());
        let io_state = self.io.clone();
        let redis_url = self.redis_url.clone();
        let host = host.to_owned();
        let locals = pyo3_async_runtimes::tokio::get_current_locals(py)?;

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            if let Some(redis_url) = redis_url {
                let client = redis::Client::open(redis_url)
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                let adapter = RedisAdapterCtr::new_with_redis(&client)
                    .await
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
                let (layer, io) = SocketIo::builder()
                    .with_adapter::<RedisAdapter<_>>(adapter)
                    .build_layer();
                serve_with_io(layer, io, handlers, io_state, locals, host, port).await
            } else {
                let (layer, io) = SocketIo::new_layer();
                serve_with_io(layer, io, handlers, io_state, locals, host, port).await
            }
        })
    }
}

async fn serve_with_io<A: Adapter + socketioxide_core::adapter::DefinedAdapter>(
    layer: SocketIoLayer<A>,
    io: SocketIo<A>,
    handlers: Arc<NamespaceHandlers>,
    io_state: Arc<RwLock<Option<Arc<dyn IoOperations>>>>,
    locals: TaskLocals,
    host: String,
    port: u16,
) -> PyResult<()> {
    *io_state
        .write()
        .map_err(|_| PyRuntimeError::new_err("native server state is poisoned"))? =
        Some(Arc::new(io.clone()));
    for (namespace, namespace_handlers) in handlers.iter() {
        let connection_handlers = namespace_handlers.clone();
        let connection_locals = locals.clone();
        io.ns(
            namespace.clone(),
            async move |socket: SocketRef<A>, Data::<WireValue>(auth)| {
                if let Some(callback) = connection_handlers.get("connect")
                    && let Err(error) = invoke_handler(
                        callback.clone(),
                        connection_locals.clone(),
                        socket.id.to_string(),
                        auth,
                    )
                    .await
                {
                    Python::attach(|py| error.print(py));
                }
                if let Some(callback) = connection_handlers.get("disconnect") {
                    let callback = callback.clone();
                    let locals = connection_locals.clone();
                    socket.on_disconnect(
                        async move |socket: SocketRef<A>, reason: DisconnectReason| {
                            if let Err(error) = invoke_handler(
                                callback.clone(),
                                locals.clone(),
                                socket.id.to_string(),
                                WireValue::String(reason.to_string()),
                            )
                            .await
                            {
                                Python::attach(|py| error.print(py));
                            }
                        },
                    );
                }
                for (event, callback) in connection_handlers
                    .iter()
                    .filter(|(event, _)| *event != "connect" && *event != "disconnect")
                {
                    let callback = callback.clone();
                    let locals = connection_locals.clone();
                    let event = event.clone();
                    socket.on(
                        event,
                        async move |socket: SocketRef<A>,
                                    TryData::<WireValue>(data),
                                    ack: AckSender<A>| {
                            let data = data.unwrap_or(WireValue::Null);
                            let response = invoke_handler(
                                callback.clone(),
                                locals.clone(),
                                socket.id.to_string(),
                                data,
                            )
                            .await
                            .unwrap_or_else(|error| {
                                Python::attach(|py| error.print(py));
                                WireValue::Object(vec![(
                                    "error".to_owned(),
                                    WireValue::String("native handler failed".to_owned()),
                                )])
                            });
                            ack.send(&response).ok();
                        },
                    );
                }
            },
        )
        .spawn();
    }

    let app = axum::Router::new().layer(layer);
    let listener = tokio::net::TcpListener::bind((host.as_str(), port))
        .await
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    axum::serve(listener, app)
        .await
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok(())
}

impl NativeServer {
    fn running_io(&self) -> PyResult<Arc<dyn IoOperations>> {
        self.io
            .read()
            .map_err(|_| PyRuntimeError::new_err("native server state is poisoned"))?
            .clone()
            .ok_or_else(|| PyRuntimeError::new_err("native server is not running"))
    }
}
