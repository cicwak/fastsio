import base64

import pytest
from engineio import json, packet as python_packet

from fastsio.rust_engineio import Packet, install, uninstall


def test_text_packet_matches_python_engineio():
    python_packet.Packet.json = json
    Packet.json = json
    encoded = '4{"message":"hello","sequence":1}'
    native = Packet(encoded_packet=encoded)
    python = python_packet.Packet(encoded_packet=encoded)
    assert (native.packet_type, native.data, native.binary) == (
        python.packet_type,
        python.data,
        python.binary,
    )
    assert native.encode() == python.encode()


def test_binary_round_trip():
    native = Packet(packet_type=4, data=b"hello")
    encoded = native.encode(b64=True)
    decoded = Packet(encoded_packet=encoded)
    assert decoded.packet_type == 4
    assert decoded.data == b"hello"
    assert decoded.binary is True


@pytest.mark.parametrize(
    "encoded",
    [
        "2",
        "4hello",
        '4{"message":"hello","sequence":1}',
        '4["hello",1,true]',
        "4123",
        b"hello",
        bytearray(b"hello"),
        "b" + base64.b64encode(b"hello").decode(),
    ],
)
def test_decode_matches_python_engineio(encoded):
    python_packet.Packet.json = json
    Packet.json = json
    native = Packet(encoded_packet=encoded)
    python = python_packet.Packet(encoded_packet=encoded)
    assert (native.packet_type, native.data, native.binary) == (
        python.packet_type,
        python.data,
        python.binary,
    )


def test_install_can_be_reverted():
    previous = install()
    try:
        assert python_packet.Packet is Packet
    finally:
        uninstall(previous)
    assert python_packet.Packet is previous
