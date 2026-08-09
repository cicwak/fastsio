use std::fmt;

use pyo3::{
    exceptions::{PyOverflowError, PyTypeError},
    prelude::*,
    types::{PyBool, PyByteArray, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple},
};
use pythonize::pythonize;
use serde::{
    Deserialize, Deserializer, Serialize, Serializer,
    de::{MapAccess, SeqAccess, Visitor},
    ser::{SerializeMap, SerializeSeq},
};

/// A dynamic Socket.IO payload that preserves binary values at any depth.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum WireValue {
    Null,
    Bool(bool),
    I64(i64),
    U64(u64),
    F64(f64),
    String(String),
    Bytes(Vec<u8>),
    Array(Vec<Self>),
    Object(Vec<(String, Self)>),
}

impl WireValue {
    pub(crate) fn into_python(self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match self {
            Self::Null => Ok(py.None()),
            Self::Bool(value) => primitive_to_python(py, serde_json::Value::Bool(value)),
            Self::I64(value) => primitive_to_python(py, serde_json::Value::from(value)),
            Self::U64(value) => primitive_to_python(py, serde_json::Value::from(value)),
            Self::F64(value) => primitive_to_python(py, serde_json::Value::from(value)),
            Self::String(value) => primitive_to_python(py, serde_json::Value::String(value)),
            Self::Bytes(value) => Ok(PyBytes::new(py, &value).into_any().unbind()),
            Self::Array(values) => {
                let result = PyList::empty(py);
                for value in values {
                    result.append(value.into_python(py)?.bind(py))?;
                }
                Ok(result.into_any().unbind())
            }
            Self::Object(values) => {
                let result = PyDict::new(py);
                for (key, value) in values {
                    result.set_item(key, value.into_python(py)?.bind(py))?;
                }
                Ok(result.into_any().unbind())
            }
        }
    }

    pub(crate) fn from_python(value: &Bound<'_, PyAny>) -> PyResult<Self> {
        if value.is_none() {
            return Ok(Self::Null);
        }
        if value.is_instance_of::<PyBool>() {
            return Ok(Self::Bool(value.extract()?));
        }
        if let Ok(value) = value.cast::<PyBytes>() {
            return Ok(Self::Bytes(value.as_bytes().to_vec()));
        }
        if let Ok(value) = value.cast::<PyByteArray>() {
            // The GIL is held for the duration of the copy.
            return Ok(Self::Bytes(unsafe { value.as_bytes() }.to_vec()));
        }
        if value.is_instance_of::<PyInt>() {
            if let Ok(integer) = value.extract::<i64>() {
                return Ok(Self::I64(integer));
            }
            if let Ok(integer) = value.extract::<u64>() {
                return Ok(Self::U64(integer));
            }
            return Err(PyOverflowError::new_err(
                "integer is outside the signed/unsigned 64-bit Socket.IO range",
            ));
        }
        if value.is_instance_of::<PyFloat>() {
            return Ok(Self::F64(value.extract()?));
        }
        if value.is_instance_of::<PyString>() {
            return Ok(Self::String(value.extract()?));
        }
        if let Ok(values) = value.cast::<PyList>() {
            return values
                .iter()
                .map(|value| Self::from_python(&value))
                .collect::<PyResult<Vec<_>>>()
                .map(Self::Array);
        }
        if let Ok(values) = value.cast::<PyTuple>() {
            return values
                .iter()
                .map(|value| Self::from_python(&value))
                .collect::<PyResult<Vec<_>>>()
                .map(Self::Array);
        }
        if let Ok(values) = value.cast::<PyDict>() {
            let mut result = Vec::with_capacity(values.len());
            for (key, value) in values.iter() {
                result.push((key.extract::<String>()?, Self::from_python(&value)?));
            }
            return Ok(Self::Object(result));
        }
        Err(PyTypeError::new_err(format!(
            "unsupported Socket.IO payload type: {}",
            value.get_type().name()?
        )))
    }
}

fn primitive_to_python(py: Python<'_>, value: serde_json::Value) -> PyResult<Py<PyAny>> {
    Ok(pythonize(py, &value)?.unbind())
}

impl Serialize for WireValue {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match self {
            Self::Null => serializer.serialize_none(),
            Self::Bool(value) => serializer.serialize_bool(*value),
            Self::I64(value) => serializer.serialize_i64(*value),
            Self::U64(value) => serializer.serialize_u64(*value),
            Self::F64(value) => serializer.serialize_f64(*value),
            Self::String(value) => serializer.serialize_str(value),
            Self::Bytes(value) => serializer.serialize_bytes(value),
            Self::Array(values) => {
                let mut sequence = serializer.serialize_seq(Some(values.len()))?;
                for value in values {
                    sequence.serialize_element(value)?;
                }
                sequence.end()
            }
            Self::Object(values) => {
                let mut map = serializer.serialize_map(Some(values.len()))?;
                for (key, value) in values {
                    map.serialize_entry(key, value)?;
                }
                map.end()
            }
        }
    }
}

struct WireValueVisitor;

impl<'de> Visitor<'de> for WireValueVisitor {
    type Value = WireValue;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON-compatible value or binary payload")
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E> {
        Ok(WireValue::Null)
    }

    fn visit_none<E>(self) -> Result<Self::Value, E> {
        Ok(WireValue::Null)
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E> {
        Ok(WireValue::Bool(value))
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E> {
        Ok(WireValue::I64(value))
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E> {
        Ok(WireValue::U64(value))
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E> {
        Ok(WireValue::F64(value))
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E> {
        Ok(WireValue::String(value.to_owned()))
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E> {
        Ok(WireValue::String(value))
    }

    fn visit_bytes<E>(self, value: &[u8]) -> Result<Self::Value, E> {
        Ok(WireValue::Bytes(value.to_vec()))
    }

    fn visit_byte_buf<E>(self, value: Vec<u8>) -> Result<Self::Value, E> {
        Ok(WireValue::Bytes(value))
    }

    fn visit_seq<A>(self, mut sequence: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut result = Vec::with_capacity(sequence.size_hint().unwrap_or_default());
        while let Some(value) = sequence.next_element()? {
            result.push(value);
        }
        Ok(WireValue::Array(result))
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut result = Vec::with_capacity(map.size_hint().unwrap_or_default());
        while let Some((key, value)) = map.next_entry()? {
            result.push((key, value));
        }
        Ok(WireValue::Object(result))
    }
}

impl<'de> Deserialize<'de> for WireValue {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(WireValueVisitor)
    }
}

#[cfg(test)]
mod tests {
    use super::WireValue;

    #[test]
    fn json_round_trip() {
        let value = serde_json::from_str::<WireValue>(r#"{"nested":[true,42,null]}"#).unwrap();
        assert_eq!(
            serde_json::to_string(&value).unwrap(),
            r#"{"nested":[true,42,null]}"#
        );
    }
}
