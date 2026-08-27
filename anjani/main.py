"""Anjani main entry point"""
# Copyright (C) 2020 - 2023 UserbotIndo Team
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.

import asyncio
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import aiorun
import colorlog
import dotenv

from . import DEFAULT_CONFIG_PATH
from .core import Anjani
from .util.config import Config


log = logging.getLogger("launch")


# ============================================================
# Koyeb Health Check Server
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):
    """Simple HTTP health-check endpoint for Koyeb."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()

    def log_message(self, format, *args):
        # Disable HTTP request logging
        pass


def start_health_server():
    """Start HTTP server required by Koyeb Web Service."""

    port = int(os.environ.get("PORT", "8000"))

    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)

        log.info(
            "Koyeb health server listening on 0.0.0.0:%s",
            port,
        )

        server.serve_forever()

    except Exception:
        log.exception("Health server failed to start")


# ============================================================
# Logging
# ============================================================

def _level_check(level: str) -> int:
    _str_to_lvl = {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
    }

    if level not in _str_to_lvl:
        return logging.INFO

    return _str_to_lvl[level]


def _setup_log() -> None:
    """Configures logging."""

    level = _level_check(
        os.environ.get("LOG_LEVEL", "info").upper()
    )

    logging.root.setLevel(level)

    # Color log config
    log_color: bool = os.environ.get("LOG_COLOR") in {
        "enable",
        1,
        "1",
        "true",
    }

    file_format = (
        "[ %(asctime)s: %(levelname)-8s ] "
        "%(name)-15s - %(message)s"
    )

    logfile = logging.FileHandler("Anjani.log")

    formatter = logging.Formatter(
        file_format,
        datefmt="%H:%M:%S",
    )

    logfile.setFormatter(formatter)
    logfile.setLevel(level)

    if log_color:
        formatter = colorlog.ColoredFormatter(
            "  %(log_color)s%(levelname)-8s%(reset)s  |  "
            "%(name)-15s  |  "
            "%(log_color)s%(message)s%(reset)s"
        )
    else:
        formatter = logging.Formatter(
            "  %(levelname)-8s  |  "
            "%(name)-15s  |  "
            "%(message)s"
        )

    stream = logging.StreamHandler()
    stream.setLevel(level)
    stream.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)

    root.addHandler(stream)
    root.addHandler(logfile)

    # Logging necessary for selected libs
    aiorun.logger.disabled = True

    logging.getLogger("pymongo").setLevel(
        logging.WARNING
    )

    logging.getLogger("pyrogram").setLevel(
        logging.ERROR
    )

    logging.getLogger("urllib3").setLevel(
        logging.WARNING
    )


# ============================================================
# Main
# ============================================================

def start() -> None:
    """Main entry point for the bot."""

    config_path = Path(DEFAULT_CONFIG_PATH)

    if config_path.is_file():
        dotenv.load_dotenv(config_path)

    _setup_log()

    log.info(
        "Running on Python %s.%s.%s",
        sys.version_info.major,
        sys.version_info.minor,
        sys.version_info.micro,
    )

    log.info("Loading code")

    # ========================================================
    # Event loop
    # ========================================================

    _uvloop = False

    if sys.platform == "win32":

        policy = asyncio.WindowsProactorEventLoopPolicy()
        asyncio.set_event_loop_policy(policy)

    else:

        try:
            import uvloop  # type: ignore

        except ImportError:
            pass

        else:
            uvloop.install()
            _uvloop = True

            log.info("Using uvloop event loop")

    # ========================================================
    # Initialize bot
    # ========================================================

    log.info("Initializing bot")

    loop = asyncio.new_event_loop()

    # ========================================================
    # Start Koyeb health server
    # ========================================================

    health_thread = threading.Thread(
        target=start_health_server,
        name="KoyebHealthServer",
        daemon=True,
    )

    health_thread.start()

    log.info("Koyeb health server started")

    # ========================================================
    # Start Anjani
    # ========================================================

    aiorun.run(
        Anjani.init_and_run(
            Config(),
            loop=loop,
        ),
        loop=loop if _uvloop else None,
    )


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    start()
