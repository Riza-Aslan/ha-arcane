"""Data update coordinator for Arcane: all environments (hosts) in one poll."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArcaneAuthError, ArcaneClient, ArcaneError
from .const import (
    CONF_ENVIRONMENTS,
    CONF_STALE_HOURS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_HOURS,
    DOMAIN,
    INSTANCE,
    SEP,
)

_LOGGER = logging.getLogger(__name__)

type ArcaneConfigEntry = ConfigEntry[ArcaneCoordinator]

# While Docker recreates a container, the old one is renamed "<12 hex>_<name>".
# That is a transient artefact, not a real container: never create entities for it.
_RECREATE_NAME = re.compile(r"^[0-9a-f]{12}_")


@dataclass
class EnvData:
    """Snapshot of one Arcane environment (= one Docker host)."""

    id: str
    name: str
    is_edge: bool = False
    ok: bool = False  # last fetch worked; False = offline/disabled/failed
    container_counts: dict[str, Any] = field(default_factory=dict)
    project_counts: dict[str, Any] = field(default_factory=dict)
    image_updates: dict[str, Any] = field(default_factory=dict)
    containers: dict[str, dict] = field(default_factory=dict)  # key: container name
    projects: dict[str, dict] = field(default_factory=dict)  # key: project name


@dataclass
class ArcaneData:
    version: dict[str, Any] = field(default_factory=dict)
    environments: dict[str, EnvData] = field(default_factory=dict)


def container_name(raw: dict) -> str:
    names = raw.get("names") or []
    if names:
        return str(names[0]).lstrip("/")
    return str(raw.get("id", ""))[:12]


class ArcaneCoordinator(DataUpdateCoordinator[ArcaneData]):
    """Polls Arcane and tidies up entities of things that no longer exist."""

    config_entry: ArcaneConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ArcaneConfigEntry, client: ArcaneClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client
        self._store: Store[dict[str, float]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.missing"
        )
        self._missing: dict[str, float] = {}  # entity_id -> first time seen missing

    async def _async_setup(self) -> None:
        self._missing = await self._store.async_load() or {}

    # --- polling ------------------------------------------------------------

    async def _fetch_env(self, raw: dict, prev: EnvData | None) -> EnvData:
        env = EnvData(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            is_edge=bool(raw.get("isEdge")),
        )
        # keep the last known items so their entities turn unavailable instead of vanishing
        if prev is not None:
            env.container_counts = prev.container_counts
            env.project_counts = prev.project_counts
            env.image_updates = prev.image_updates
            env.containers = prev.containers
            env.projects = prev.projects
        if not raw.get("enabled", True) or raw.get("status", "online") != "online":
            return env
        try:
            containers, projects, cc, pc, iu = await asyncio.gather(
                self.client.containers(env.id),
                self.client.projects(env.id),
                self.client.container_counts(env.id),
                self.client.project_counts(env.id),
                self.client.image_update_summary(env.id),
            )
        except ArcaneError as err:  # incl. a 403 for just this environment
            _LOGGER.debug("Environment %s (%s) not readable: %s", env.name, env.id, err)
            return env
        env.ok = True
        env.container_counts, env.project_counts, env.image_updates = cc, pc, iu
        env.containers = {
            n: c for c in containers if not _RECREATE_NAME.match(n := container_name(c))
        }
        env.projects = {p["name"]: p for p in projects if p.get("name")}
        return env

    async def _async_update_data(self) -> ArcaneData:
        try:
            raw_envs = await self.client.environments()
        except ArcaneAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ArcaneError as err:
            raise UpdateFailed(f"Arcane not reachable: {err}") from err

        wanted = {str(i) for i in (self.config_entry.options.get(CONF_ENVIRONMENTS) or [])}
        raw_envs = [e for e in raw_envs if not wanted or str(e["id"]) in wanted]
        prev = self.data.environments if self.data else {}
        results = await asyncio.gather(
            *(self._fetch_env(e, prev.get(str(e["id"]))) for e in raw_envs)
        )
        data = ArcaneData(
            version=self.data.version if self.data else {},
            environments={e.id: e for e in results},
        )
        try:
            data.version = await self.client.app_version()
        except ArcaneError:
            pass
        await self._async_cleanup(data)
        return data

    # --- tidy up ------------------------------------------------------------

    def _stale_entities(self, data: ArcaneData) -> dict[str, str]:
        """entity_id -> reason, for entities whose item verifiably no longer exists.

        An item only counts as gone while its host answered this poll (env.ok);
        an offline host never causes removals.
        """
        ent_reg = er.async_get(self.hass)
        stale: dict[str, str] = {}
        eid = self.config_entry.entry_id
        for ent in er.async_entries_for_config_entry(ent_reg, eid):
            parts = (ent.unique_id or "").split(SEP, 3)
            if len(parts) < 3 or parts[0] != eid or parts[1] == INSTANCE:
                continue
            env = data.environments.get(parts[1])
            if env is None:
                stale[ent.entity_id] = "host removed or not selected"
            elif env.ok and len(parts) == 4:
                kind, name = parts[2], parts[3]
                items = env.containers if kind.startswith("container") else env.projects
                if name not in items:
                    stale[ent.entity_id] = f"{kind} {name} gone"
        return stale

    async def _async_cleanup(self, data: ArcaneData, force: bool = False) -> None:
        hours = self.config_entry.options.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS)
        if not force and not hours:
            return
        now = time.time()
        stale = self._stale_entities(data)
        changed = False
        for ent_id in [k for k in self._missing if k not in stale]:
            del self._missing[ent_id]  # came back (e.g. after a redeploy) or was removed
            changed = True
        for ent_id in stale:
            if ent_id not in self._missing:
                self._missing[ent_id] = now
                changed = True
        ent_reg = er.async_get(self.hass)
        for ent_id, since in list(self._missing.items()):
            if force or now - since >= hours * 3600:
                _LOGGER.info("Removing %s (%s)", ent_id, stale.get(ent_id))
                ent_reg.async_remove(ent_id)
                del self._missing[ent_id]
                changed = True
        self._remove_empty_devices(data)
        if changed:
            await self._store.async_save(self._missing)

    def _remove_empty_devices(self, data: ArcaneData) -> None:
        dev_reg = dr.async_get(self.hass)
        ent_reg = er.async_get(self.hass)
        eid = self.config_entry.entry_id
        for dev in dr.async_entries_for_config_entry(dev_reg, eid):
            ident = next((i for d, i in dev.identifiers if d == DOMAIN), "")
            parts = ident.split(SEP)
            if len(parts) != 2 or parts[0] != eid or parts[1] in data.environments:
                continue  # instance device, foreign device, or host still present
            if not er.async_entries_for_device(
                ent_reg, dev.id, include_disabled_entities=True
            ):
                dev_reg.async_remove_device(dev.id)

    async def async_cleanup_now(self) -> int:
        """Remove everything that is verifiably gone right now, ignoring the grace period."""
        count = len(self._stale_entities(self.data))
        await self._async_cleanup(self.data, force=True)
        return count
