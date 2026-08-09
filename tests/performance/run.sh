#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

python "$SCRIPT_DIR/text_packet.py"
python "$SCRIPT_DIR/binary_packet.py"
python "$SCRIPT_DIR/json_packet.py"
python "$SCRIPT_DIR/namespace_packet.py"
python "$SCRIPT_DIR/server_receive.py"
python "$SCRIPT_DIR/server_send.py"
python "$SCRIPT_DIR/server_send_broadcast.py"
