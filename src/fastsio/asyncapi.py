import inspect

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union, get_args, get_origin

try:  # pragma: no cover - optional
    from pydantic import BaseModel as _PydanticBaseModel  # type: ignore
except Exception:  # pragma: no cover - pydantic optional for type checking only
    _PydanticBaseModel = object  # type: ignore


@dataclass
class AsyncAPIConfig:
    enabled: bool = True
    url: str = "/asyncapi.json"
    expose_yaml: bool = True
    title: str = "Socket.IO API"
    version: str = "1.0.0"
    description: Optional[str] = None
    servers: Dict[str, Any] = field(default_factory=dict)
    channel_prefix: str = ""
    ui_url: str | None = "/asyncapi"


class AsyncAPIGenerator:
    def __init__(self, config: AsyncAPIConfig) -> None:
        self.config = config
        self._components: Dict[str, Any] = {}

    def generate(self, server: Any) -> Dict[str, Any]:
        channels: Dict[str, Any] = {}
        operations: Dict[str, Any] = {}

        # server.handlers: Dict[namespace, Dict[event, handler]]
        all_handlers: List[Tuple[str, str, Any]] = []
        for ns, event_to_handler in getattr(server, "handlers", {}).items():
            for event, handler in event_to_handler.items():
                all_handlers.append((ns or "/", event, handler))

        # Skip reserved events
        reserved: List[str] = getattr(
            server, "reserved_events", ["connect", "disconnect"]
        )  # type: ignore

        for namespace, event, handler in all_handlers:
            if event in reserved:
                continue

            # Check if handler has response_model dict
            response_model = getattr(handler, "_fastsio_response_model", None)

            if isinstance(response_model, dict):
                # For response_model dict, create separate channels for each event
                for response_event_name, model in response_model.items():
                    response_address = self._build_channel_name(
                        namespace, response_event_name, handler
                    )
                    response_channel_key = self._sanitize_key(response_address)
                    response_ch_obj = channels.setdefault(
                        response_channel_key, {"address": response_address}
                    )
                    response_messages_map: Dict[str, Any] = response_ch_obj.setdefault(
                        "messages", {}
                    )

                    # Create response schema for this specific event
                    model_schema = self._to_schema(model)
                    if model_schema:
                        resp_msg_key = self._sanitize_key(
                            f"{response_event_name}Response"
                        )
                        response_messages_map[resp_msg_key] = {
                            "name": f"{response_event_name}:response",
                            "payload": model_schema,
                        }
                        op_id = f"{response_channel_key}__send"
                        operations[op_id] = {
                            "action": "send",
                            "channel": {
                                "$ref": f"#/channels/{self._escape_ref(response_channel_key)}"
                            },
                            "messages": [
                                {
                                    "$ref": f"#/channels/{self._escape_ref(response_channel_key)}/messages/{self._escape_ref(resp_msg_key)}"
                                }
                            ],
                        }

                # Still create the receive channel for the original handler event
                address = self._build_channel_name(namespace, event, handler)
                channel_key = self._sanitize_key(address)
                ch_obj = channels.setdefault(channel_key, {"address": address})
                messages_map: Dict[str, Any] = ch_obj.setdefault("messages", {})

                request_schema = self._infer_request_schema(handler)
                if request_schema is not None:
                    req_msg_key = self._sanitize_key(f"{event}Request")
                    messages_map[req_msg_key] = {
                        "name": f"{event}:request",
                        "payload": request_schema,
                    }
                    op_id = f"{channel_key}__receive"
                    operations[op_id] = {
                        "action": "receive",
                        "channel": {
                            "$ref": f"#/channels/{self._escape_ref(channel_key)}"
                        },
                        "messages": [
                            {
                                "$ref": f"#/channels/{self._escape_ref(channel_key)}/messages/{self._escape_ref(req_msg_key)}"
                            }
                        ],
                    }
            else:
                # Original behavior for single response_model or no response_model
                address = self._build_channel_name(namespace, event, handler)
                channel_key = self._sanitize_key(address)
                # Ensure channel exists with required address (v3)
                ch_obj = channels.setdefault(channel_key, {"address": address})
                # Prepare channel-level messages map
                messages_map: Dict[str, Any] = ch_obj.setdefault("messages", {})

                request_schema = self._infer_request_schema(handler)
                response_schema = self._infer_response_schema(handler)

                # In AsyncAPI v3: operations have action send/receive
                # - Server application receives messages from clients (client->server): action = "receive"
                # - Server application sends messages to clients (server->client): action = "send"
                if request_schema is not None:
                    req_msg_key = self._sanitize_key(f"{event}Request")
                    messages_map[req_msg_key] = {
                        "name": f"{event}:request",
                        "payload": request_schema,
                    }
                    op_id = f"{channel_key}__receive"
                    operations[op_id] = {
                        "action": "receive",
                        "channel": {
                            "$ref": f"#/channels/{self._escape_ref(channel_key)}"
                        },
                        "messages": [
                            {
                                "$ref": f"#/channels/{self._escape_ref(channel_key)}/messages/{self._escape_ref(req_msg_key)}"
                            }
                        ],
                    }
                if response_schema is not None:
                    resp_msg_key = self._sanitize_key(f"{event}Response")
                    messages_map[resp_msg_key] = {
                        "name": f"{event}:response",
                        "payload": response_schema,
                    }
                    op_id = f"{channel_key}__send"
                    operations[op_id] = {
                        "action": "send",
                        "channel": {
                            "$ref": f"#/channels/{self._escape_ref(channel_key)}"
                        },
                        "messages": [
                            {
                                "$ref": f"#/channels/{self._escape_ref(channel_key)}/messages/{self._escape_ref(resp_msg_key)}"
                            }
                        ],
                    }

        doc: Dict[str, Any] = {
            "asyncapi": "3.0.0",
            "info": {
                "title": self.config.title,
                "version": self.config.version,
            },
            "channels": channels,
        }
        if operations:
            doc["operations"] = operations
        if self.config.description:
            doc["info"]["description"] = self.config.description
        if self.config.servers:
            doc["servers"] = self.config.servers
        if self._components:
            doc["components"] = {"schemas": self._components}
        return doc

    def _escape_ref(self, key: str) -> str:
        # Per JSON Pointer, slashes in keys must be escaped when used in refs
        # Channels keys often include slashes; replace with ~1 per RFC6901
        return key.replace("~", "~0").replace("/", "~1")

    def _sanitize_key(self, value: str) -> str:
        # Create a safe key for map entries: allow alnum, replace others with '_'
        out_chars: List[str] = []
        for ch in value:
            if ch.isalnum() or ch in ("_",):
                out_chars.append(ch)
            else:
                out_chars.append("_")
        # Avoid empty key
        key = "".join(out_chars).strip("_") or "channel"
        return key

    def _build_channel_name(self, namespace: str, event: str, handler: Any) -> str:
        override = getattr(handler, "_fastsio_channel_override", None)
        if override:
            base = str(override)
        else:
            ns_part = namespace if namespace and namespace != "/" else ""
            base = f"{ns_part}{event}"
        prefix = self.config.channel_prefix or ""
        return f"{prefix}{base}"

    def _infer_request_schema(self, handler: Any) -> Optional[Dict[str, Any]]:
        try:
            sig = inspect.signature(handler)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

        di_annos: Tuple[Any, ...] = self._get_di_annotations()

        for param in sig.parameters.values():
            ann = param.annotation
            if ann is inspect.Signature.empty:
                continue
            # Skip known DI types
            if ann in di_annos:
                continue
            # Treat the first non-DI annotation as the payload
            return self._to_schema(ann)
        return None

    def _infer_response_schema(self, handler: Any) -> Optional[Dict[str, Any]]:
        # Priority: explicit attribute set via decorator > return annotation
        resp_model = getattr(handler, "_fastsio_response_model", None)
        if resp_model is not None:
            # If response_model is a dictionary, return None since we handle it separately
            if isinstance(resp_model, dict):
                return None
            # Single response model (existing behavior)
            return self._to_schema(resp_model)

        try:
            sig = inspect.signature(handler)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        ret = sig.return_annotation
        if ret is inspect.Signature.empty or ret is None:
            return None
        return self._to_schema(ret)

    def _get_di_annotations(self) -> Tuple[Any, ...]:
        # Import locally to avoid cycles
        try:
            from .async_server import AsyncServer as _AsyncServerType  # type: ignore
            from .types import (  # type: ignore
                Auth,
                Data,
                Environ,
                Event,
                Reason,
                SocketID,
            )
        except Exception:  # pragma: no cover
            _AsyncServerType = object  # type: ignore
            SocketID = object  # type: ignore
            Environ = object  # type: ignore
            Auth = object  # type: ignore
            Reason = object  # type: ignore
            Data = object  # type: ignore
            Event = object  # type: ignore
        return (
            _AsyncServerType,
            SocketID,
            Environ,
            Auth,
            Reason,
            Data,
            Event,
        )

    def _to_schema(self, typ: Any) -> Dict[str, Any]:
        # Pydantic models
        try:
            if isinstance(typ, type) and issubclass(typ, _PydanticBaseModel):  # type: ignore[arg-type]
                name = typ.__name__
                if name not in self._components:
                    # Pydantic v2: model_json_schema, v1: schema
                    if hasattr(typ, "model_json_schema"):
                        schema = typ.model_json_schema()  # type: ignore[attr-defined]
                    else:
                        schema = typ.schema(ref_template="#/components/schemas/{model}")  # type: ignore[attr-defined]
                    self._components[name] = schema
                return {"$ref": f"#/components/schemas/{name}"}
        except Exception:
            pass

        origin = get_origin(typ)
        args = get_args(typ)

        if origin is Union:
            # Flatten Optional[X] to oneOf [X, null]
            variants: List[Any] = list(args)
            schemas: List[Dict[str, Any]] = []
            for v in variants:
                if v is type(None):
                    schemas.append({"type": "null"})
                else:
                    schemas.append(self._to_schema(v))
            return {"oneOf": schemas}

        if origin in (list, List):
            item_type = args[0] if args else Any
            return {"type": "array", "items": self._to_schema(item_type)}

        if origin in (dict, Dict):
            value_type = args[1] if len(args) == 2 else Any
            return {
                "type": "object",
                "additionalProperties": self._to_schema(value_type),
            }

        # Primitives
        python_to_json: Dict[Any, str] = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            bytes: "string",
        }
        if typ in python_to_json:
            if typ is bytes:
                return {"type": "string", "format": "byte"}
            return {"type": python_to_json[typ]}

        if typ is Any:
            return {}

        # Fallback to string
        return {"type": "string"}
