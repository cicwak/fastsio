"""Opt-in Rust acceleration for python-engineio's protocol packet codec."""

import base64

from engineio import json

from . import _rust

NOOP = 6
MESSAGE = 4


class Packet:
    """Engine.IO packet with the same public shape as python-engineio.Packet."""

    json = json

    def __init__(self, packet_type=NOOP, data=None, encoded_packet=None):
        self.packet_type = packet_type
        self.data = data
        self.encode_cache = None
        self.binary = isinstance(data, (bytes, bytearray))
        if self.binary and self.packet_type != MESSAGE:
            raise ValueError("Binary packets can only be of type MESSAGE")
        if encoded_packet is not None:
            self.decode(encoded_packet)

    def encode(self, b64=False):
        """Encode an Engine.IO packet, preserving python-engineio caching."""
        if self.encode_cache:
            return self.encode_cache
        if self.json is json:
            encoded = _rust.encode_engineio(self.packet_type, self.data, b64)
        elif self.binary:
            encoded = (
                "b" + base64.b64encode(self.data).decode("utf-8")
                if b64
                else self.data
            )
        else:
            encoded = str(self.packet_type)
            if isinstance(self.data, str):
                encoded += self.data
            elif isinstance(self.data, (dict, list)):
                encoded += self.json.dumps(self.data, separators=(",", ":"))
            elif self.data is not None:
                encoded += str(self.data)
        self.encode_cache = encoded
        return encoded

    def decode(self, encoded_packet):
        """Decode an Engine.IO packet."""
        if self.json is json:
            self.packet_type, self.data, self.binary = _rust.decode_engineio(
                encoded_packet
            )
            return

        self.binary = isinstance(encoded_packet, (bytes, bytearray))
        if not self.binary and len(encoded_packet) == 0:
            raise ValueError("Invalid empty packet received")
        b64 = not self.binary and encoded_packet[0] == "b"
        if b64:
            self.binary = True
            self.packet_type = MESSAGE
            self.data = base64.b64decode(encoded_packet[1:])
        elif self.binary:
            self.packet_type = MESSAGE
            self.data = bytes(encoded_packet)
        else:
            self.packet_type = int(encoded_packet[0])
            try:
                if encoded_packet[1].isnumeric():
                    raise ValueError
                self.data = self.json.loads(encoded_packet[1:])
            except (ValueError, IndexError):
                self.data = encoded_packet[1:]


def install():
    """Install the native packet class into python-engineio process-wide."""
    import engineio.packet

    previous = engineio.packet.Packet
    engineio.packet.Packet = Packet
    return previous


def uninstall(previous):
    """Restore a packet class returned by :func:`install`."""
    import engineio.packet

    engineio.packet.Packet = previous
