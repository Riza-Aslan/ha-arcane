"""Constants for the Arcane integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "arcane"

CONF_API_KEY = "api_key"
CONF_ENVIRONMENT_ID = "environment_id"  # only used by the v1 -> v2 migration
CONF_ENVIRONMENTS = "environments"  # option: limit to these environment ids (empty = all)
CONF_STALE_HOURS = "stale_hours"  # option: grace period before removal (0 = never)

CONF_SCAN_INTERVAL = "scan_interval"  # option: seconds between polls
CONF_EVENTS = "events"  # option: fire events on the bus

DEFAULT_STALE_HOURS = 24
DEFAULT_SCAN_SECONDS = 60
DEFAULT_SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_SECONDS)
PAGE_SIZE = 100

# Entity registry unique_id parts are joined with this separator:
#   <entry_id>|<environment_id>|<kind>|<name>   containers and projects
#   <entry_id>|<environment_id>|<key>           per-host sensors/buttons
#   <entry_id>|instance|<key>                   entities of the Arcane instance itself
SEP = "|"
INSTANCE = "instance"

SERVICE_CHECK_UPDATES = "check_updates"
SERVICE_UPDATE_ALL = "update_all"
SERVICE_REDEPLOY = "redeploy"
ATTR_DRY_RUN = "dry_run"

EVENT_CONTAINER_STATE = "arcane_container_state_changed"
EVENT_PROJECT_STATE = "arcane_project_state_changed"
