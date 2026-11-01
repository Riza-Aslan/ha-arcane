"""Diagnostics: structure and counts only, no names, URLs or keys."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import ArcaneConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ArcaneConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data.data
    return {
        "options": dict(entry.options),
        "arcane_version": data.version.get("currentVersion"),
        "environments": [
            {
                "ok": env.ok,
                "status": env.status,
                "edge": env.is_edge,
                "containers": len(env.containers),
                "projects": len(env.projects),
                "container_counts": env.container_counts,
                "image_counts": env.image_counts,
                "image_updates": env.image_updates,
                "docker_version": env.docker.get("ServerVersion"),
            }
            for env in data.environments.values()
        ],
    }
