use base64::Engine;
use base64::engine::general_purpose::STANDARD as BASE64;
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyModule};
use pythonize::{depythonize, pythonize};
use serde_json::Value;

const MESSAGE: u8 = 4;
type SocketIoDecoded = (u8, Option<String>, Option<Py<PyAny>>, Py<PyAny>, usize);

#[pyfunction]
fn decode_engineio(py: Python<'_>, encoded: &Bound<'_, PyAny>) -> PyResult<(u8, Py<PyAny>, bool)> {
    if let Ok(bytes) = encoded.extract::<Vec<u8>>() {
        return Ok((MESSAGE, PyBytes::new(py, &bytes).into_any().unbind(), true));
    }

    let value = encoded
        .extract::<String>()
        .map_err(|_| PyTypeError::new_err("encoded Engine.IO packet must be str or bytes"))?;
    if value.is_empty() {
        return Err(PyValueError::new_err("Invalid empty packet received"));
    }

    if let Some(payload) = value.strip_prefix('b') {
        let decoded = BASE64
            .decode(payload)
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        return Ok((
            MESSAGE,
            PyBytes::new(py, &decoded).into_any().unbind(),
            true,
        ));
    }

    let packet_type = value.as_bytes()[0]
        .checked_sub(b'0')
        .filter(|kind| *kind <= 6)
        .ok_or_else(|| PyValueError::new_err("Invalid Engine.IO packet type"))?;
    let payload = &value[1..];
    if payload.is_empty() {
        return Ok((
            packet_type,
            "".into_pyobject(py)?.into_any().unbind(),
            false,
        ));
    }

    // python-engineio deliberately treats a leading numeric payload as text.
    if payload.chars().next().is_some_and(char::is_numeric) {
        return Ok((
            packet_type,
            payload.into_pyobject(py)?.into_any().unbind(),
            false,
        ));
    }

    let data = match serde_json::from_str::<Value>(payload) {
        Ok(json) => pythonize(py, &json)?.unbind(),
        Err(_) => payload.into_pyobject(py)?.into_any().unbind(),
    };
    Ok((packet_type, data, false))
}

#[pyfunction]
#[pyo3(signature = (packet_type, data=None, b64=false))]
fn encode_engineio(
    py: Python<'_>,
    packet_type: u8,
    data: Option<&Bound<'_, PyAny>>,
    b64: bool,
) -> PyResult<Py<PyAny>> {
    if let Some(data) = data
        && let Ok(bytes) = data.extract::<Vec<u8>>()
    {
        if packet_type != MESSAGE {
            return Err(PyValueError::new_err(
                "Binary packets can only be of type MESSAGE",
            ));
        }
        if b64 {
            let encoded = format!("b{}", BASE64.encode(bytes));
            return Ok(encoded.into_pyobject(py)?.into_any().unbind());
        }
        return Ok(PyBytes::new(py, &bytes).into_any().unbind());
    }

    let mut encoded = packet_type.to_string();
    if let Some(data) = data
        && !data.is_none()
    {
        if let Ok(text) = data.extract::<String>() {
            encoded.push_str(&text);
        } else if data.is_instance_of::<pyo3::types::PyDict>()
            || data.is_instance_of::<pyo3::types::PyList>()
        {
            let value: Value = depythonize(data)?;
            encoded.push_str(
                &serde_json::to_string(&value)
                    .map_err(|error| PyValueError::new_err(error.to_string()))?,
            );
        } else {
            encoded.push_str(&data.str()?.to_string_lossy());
        }
    }
    Ok(encoded.into_pyobject(py)?.into_any().unbind())
}

#[pyfunction]
fn decode_socketio(py: Python<'_>, encoded: &Bound<'_, PyAny>) -> PyResult<SocketIoDecoded> {
    let packet = if let Ok(text) = encoded.extract::<String>() {
        text
    } else if let Ok(number) = encoded.extract::<i64>() {
        number.to_string()
    } else {
        return Err(PyTypeError::new_err(
            "encoded Socket.IO packet must be str or int",
        ));
    };
    let bytes = packet.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_digit() {
        return Err(PyValueError::new_err("Invalid Socket.IO packet type"));
    }

    let packet_type = bytes[0] - b'0';
    let mut index = 1;
    let mut attachments = 0usize;

    if let Some(relative_dash) = packet[index..].find('-') {
        let dash = index + relative_dash;
        let candidate = &packet[index..dash];
        if !candidate.is_empty() && candidate.bytes().all(|byte| byte.is_ascii_digit()) {
            if relative_dash > 10 {
                return Err(PyValueError::new_err("too many attachments"));
            }
            attachments = candidate
                .parse()
                .map_err(|_| PyValueError::new_err("invalid attachment count"))?;
            index = dash + 1;
        }
    }

    let mut namespace = None;
    if packet[index..].starts_with('/') {
        if let Some(relative_comma) = packet[index..].find(',') {
            let comma = index + relative_comma;
            namespace = Some(packet[index..comma].split('?').next().unwrap().to_owned());
            index = comma + 1;
        } else {
            namespace = Some(packet[index..].split('?').next().unwrap().to_owned());
            index = packet.len();
        }
    }

    let id_start = index;
    while index < packet.len()
        && packet.as_bytes()[index].is_ascii_digit()
        && index - id_start < 100
    {
        index += 1;
    }
    let id = if index > id_start {
        if index < packet.len() && packet.as_bytes()[index].is_ascii_digit() {
            return Err(PyValueError::new_err("id field is too long"));
        }
        let parsed = py
            .import("builtins")?
            .getattr("int")?
            .call1((&packet[id_start..index],))?
            .unbind();
        Some(parsed)
    } else {
        None
    };

    let data = if index < packet.len() {
        let json: Value = serde_json::from_str(&packet[index..])
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        pythonize(py, &json)?.unbind()
    } else {
        py.None()
    };
    Ok((packet_type, namespace, id, data, attachments))
}

#[pyfunction]
#[pyo3(signature = (packet_type, data=None, namespace=None, id=None))]
fn encode_socketio(
    py: Python<'_>,
    packet_type: u8,
    data: Option<&Bound<'_, PyAny>>,
    namespace: Option<String>,
    id: Option<&Bound<'_, PyAny>>,
) -> PyResult<String> {
    let mut encoded = packet_type.to_string();
    if let Some(namespace) = namespace
        && namespace != "/"
    {
        encoded.push_str(&namespace);
        encoded.push(',');
    }
    if let Some(id) = id {
        encoded.push_str(&id.str()?.to_string_lossy());
    }
    if let Some(data) = data
        && !data.is_none()
    {
        let value: Value = depythonize(data)?;
        encoded.push_str(
            &serde_json::to_string(&value)
                .map_err(|error| PyValueError::new_err(error.to_string()))?,
        );
    }
    let _ = py;
    Ok(encoded)
}

#[pymodule]
fn _rust(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_function(wrap_pyfunction!(decode_engineio, module)?)?;
    module.add_function(wrap_pyfunction!(encode_engineio, module)?)?;
    module.add_function(wrap_pyfunction!(decode_socketio, module)?)?;
    module.add_function(wrap_pyfunction!(encode_socketio, module)?)?;
    Ok(())
}
