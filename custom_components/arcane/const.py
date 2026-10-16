"""Constants for the Arcane integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "arcane"

CONF_API_KEY = "api_key"
CONF_ENVIRONMENT_ID = "environment_id"

DEFAULT_ENVIRONMENT_ID = "0"
DEFAULT_SCAN_INTERVAL = timedelta(seconds=60)
PAGE_SIZE = 100

REPO_URL = "https://github.com/getarcaneapp/arcane"
