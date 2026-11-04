#!/usr/bin/env sh
cd "$(dirname "$0")" && exec python3 -m telescopius_bridge --open "$@"
