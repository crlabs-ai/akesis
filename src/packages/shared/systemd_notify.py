"""Minimal systemd notification (sd_notify) used for watchdog pings.

Outside systemd, or when NOTIFY_SOCKET is not set, every call is a no-op. Pings are sent only
from progress points in the worker (a completed delivery or a completed pass). They are never sent
from a timer task, so a hung worker stops pinging and the watchdog can act on it.
"""

import os
import socket


def notify(message: str) -> bool:
    address = os.environ.get("NOTIFY_SOCKET", "")
    if not address:
        return False
    target = "\0" + address[1:] if address.startswith("@") else address
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode("utf-8"), target)
    except OSError:
        return False
    return True
