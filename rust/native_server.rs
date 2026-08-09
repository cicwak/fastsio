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
    extract::{AckSender, Data, SocketRef, TryData},
    socket::DisconnectReason,
};

use crate::wire_value::WireValue;

type PythonFuture = Pin<Box<dyn Future<Output = PyResult<Py<PyAny>>> + Send>>;

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
    handlers: HashMap<String, HashMap<String, Arc<Py<PyAny>>>>,
    io: Arc<RwLock<Option<SocketIo>>>,
}

#[pymethods]
impl NativeServer {
    #[new]
    fn new() -> Self {
        Self {
            handlers: HashMap::new(),
            io: Arc::new(RwLock::new(None)),
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
        let io = self.running_io()?;
        let socket = io
            .sockets()
            .into_iter()
            .find(|socket| socket.id.to_string() == sid)
            .ok_or_else(|| PyRuntimeError::new_err(format!("unknown socket id: {sid}")))?;
        socket.join(room);
        Ok(())
    }

    fn leave_room(&self, sid: &str, room: String) -> PyResult<()> {
        let io = self.running_io()?;
        let socket = io
            .sockets()
            .into_iter()
            .find(|socket| socket.id.to_string() == sid)
            .ok_or_else(|| PyRuntimeError::new_err(format!("unknown socket id: {sid}")))?;
        socket.leave(room);
        Ok(())
    }

    fn rooms(&self, sid: &str) -> PyResult<Vec<String>> {
        let io = self.running_io()?;
        let socket = io
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
        let io = self.running_io()?;
        let socket = io
            .sockets()
            .into_iter()
            .find(|socket| socket.id.to_string() == sid)
            .ok_or_else(|| PyRuntimeError::new_err(format!("unknown socket id: {sid}")))?;
        socket
            .disconnect()
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))
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
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
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

    #[pyo3(signature = (host="127.0.0.1", port=8765))]
    fn serve<'py>(&self, py: Python<'py>, host: &str, port: u16) -> PyResult<Bound<'py, PyAny>> {
        let handlers = Arc::new(self.handlers.clone());
        let io_state = self.io.clone();
        let host = host.to_owned();
        let locals = pyo3_async_runtimes::tokio::get_current_locals(py)?;

        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let (layer, io) = SocketIo::new_layer();
            *io_state
                .write()
                .map_err(|_| PyRuntimeError::new_err("native server state is poisoned"))? =
                Some(io.clone());
            for (namespace, namespace_handlers) in handlers.iter() {
                let connection_handlers = namespace_handlers.clone();
                let connection_locals = locals.clone();
                io.ns(
                    namespace.clone(),
                    async move |socket: SocketRef, Data::<WireValue>(auth)| {
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
                                async move |socket: SocketRef, reason: DisconnectReason| {
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
                                async move |socket: SocketRef,
                                            TryData::<WireValue>(data),
                                            ack: AckSender| {
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
                );
            }

            let app = axum::Router::new().layer(layer);
            let listener = tokio::net::TcpListener::bind((host.as_str(), port))
                .await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
            axum::serve(listener, app)
                .await
                .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
            Ok(())
        })
    }
}

impl NativeServer {
    fn running_io(&self) -> PyResult<SocketIo> {
        self.io
            .read()
            .map_err(|_| PyRuntimeError::new_err("native server state is poisoned"))?
            .clone()
            .ok_or_else(|| PyRuntimeError::new_err("native server is not running"))
    }
}
