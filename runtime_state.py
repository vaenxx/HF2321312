"""Ephemeral bot state shared by the running bot and its handlers."""

RUNTIME_SESSIONS: dict[int, dict] = {}
SCREENSHOT_REQUESTS: dict[int, int] = {}
