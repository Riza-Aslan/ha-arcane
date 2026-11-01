"""Data update coordinator for Arcane: all environments (hosts) in one poll."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import timedelta
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArcaneAuthError, ArcaneClient, ArcaneError, guarded
from .const import (
    CONF_ENVIRONMENTS,
    CONF_EVENTS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_SECONDS,
    EVENT_CONTAINER_STATE,
    EVENT_PROJECT_STATE,
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

# While a container is being updated, poll faster and show progress; give up after this.
UPGRADE_POLL = timedelta(seconds=15)
UPGRADE_TIMEOUT = 15 * 60

# Image update check errors are only reported as a Repair once they persist this long.
ERROR_ISSUE_AFTER = 3600


@dataclass
class _Upgrade:
    since: float
    container_id: str | None
    blocking: bool  # the API call itself runs until done (redeploy); the entity unmarks it
    saw_offline: bool = False


@dataclass
class EnvData:
    """Snapshot of one Arcane environment (= one Docker host)."""

    id: str
    name: str
    is_edge: bool = False
    status: str = ""  # as reported by Arcane: online / offline / ...
    enabled: bool = True
    ok: bool = False  # last fetch worked; False = offline/disabled/failed
    container_counts: dict[str, Any] = field(default_factory=dict)
    project_counts: dict[str, Any] = field(default_factory=dict)
    image_updates: dict[str, Any] = field(default_factory=dict)
    image_counts: dict[str, Any] = field(default_factory=dict)
    volume_counts: dict[str, Any] = field(default_factory=dict)
    network_counts: dict[str, Any] = field(default_factory=dict)
    docker: dict[str, Any] = field(default_factory=dict)
    containers: dict[str, dict] = field(default_factory=dict)  # key: container name
    projects: dict[str, dict] = field(default_factory=dict)  # key: project name


@dataclass
class ArcaneData:
    version: dict[str, Any] = field(default_factory=dict)
    environments: dict[str, EnvData] = field(default_factory=dict)


_EXIT = re.compile(r"Exited \((-?\d+)\)")


def exit_code(raw: dict) -> int | None:
    """Exit code of a stopped container, parsed from Docker's status text."""
    m = _EXIT.search(str(raw.get("status") or ""))
    return int(m.group(1)) if m else None


def health(raw: dict) -> str | None:
    """healthy / unhealthy / starting when the container has a health check, else None."""
    m = re.search(r"\((healthy|unhealthy|health: starting)\)", str(raw.get("status") or ""))
    return None if not m else ("starting" if "starting" in m.group(1) else m.group(1))


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
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_SECONDS)
            ),
        )
        self.client = client
        self._store: Store[dict[str, float]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.missing"
        )
        self._missing: dict[str, float] = {}  # entity_id -> first time seen missing
        self._upgrading: dict[tuple[str, str], _Upgrade] = {}
        self._watch: CALLBACK_TYPE | None = None
        self.manager_device_id: str | None = None  # set up before the platforms load
        self._errors_since: dict[str, float] = {}  # env_id -> first time check errors seen
        self._issues: set[str] = set()

    async def _async_setup(self) -> None:
        self._missing = await self._store.async_load() or {}

    # --- update progress ----------------------------------------------------

    def is_upgrading(self, env_id: str, name: str) -> bool:
        return (env_id, name) in self._upgrading

    @callback
    def mark_upgrading(
        self, env_id: str, name: str, container_id: str | None, *, blocking: bool
    ) -> None:
        """Show an update as in progress and poll faster until it has landed."""
        self._upgrading[(env_id, name)] = _Upgrade(time.time(), container_id, blocking)
        if self._watch is None:
            self._watch = async_track_time_interval(
                self.hass, self._watch_tick, UPGRADE_POLL, cancel_on_shutdown=True
            )
        self.async_update_listeners()

    @callback
    def unmark_upgrading(self, env_id: str, name: str) -> None:
        self._upgrading.pop((env_id, name), None)
        self._stop_watch_if_idle()
        self.async_update_listeners()

    @callback
    def _stop_watch_if_idle(self) -> None:
        if not self._upgrading and self._watch is not None:
            self._watch()
            self._watch = None

    async def _watch_tick(self, _now: Any) -> None:
        self._expire_upgrades()
        if self._upgrading:
            await self.async_request_refresh()

    @callback
    def _expire_upgrades(self) -> None:
        now = time.time()
        for key, up in list(self._upgrading.items()):
            if now - up.since > UPGRADE_TIMEOUT:
                _LOGGER.warning("Update of %s on %s did not finish in time", key[1], key[0])
                del self._upgrading[key]
        self._stop_watch_if_idle()
        self.async_update_listeners()

    @callback
    def _note_manager_offline(self) -> None:
        for up in self._upgrading.values():
            up.saw_offline = True

    @callback
    def _settle_upgrades(self, data: ArcaneData) -> None:
        """An update has landed once the container was recreated or the host came back."""
        for key, up in list(self._upgrading.items()):
            if up.blocking:
                continue
            env = data.environments.get(key[0])
            if env is not None and not env.ok:
                up.saw_offline = True
                continue
            container = env.containers.get(key[1]) if env else None
            if (
                env is None
                or container is None
                or (up.container_id and container.get("id") != up.container_id)
                or up.saw_offline
            ):
                del self._upgrading[key]
        self._expire_upgrades()

    # --- polling ------------------------------------------------------------

    async def _fetch_env(self, raw: dict, prev: EnvData | None) -> EnvData:
        env = EnvData(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            is_edge=bool(raw.get("isEdge")),
            status=str(raw.get("status") or ""),
            enabled=bool(raw.get("enabled", True)),
        )
        # keep the last known items so their entities turn unavailable instead of vanishing
        if prev is not None:
            env.container_counts = prev.container_counts
            env.project_counts = prev.project_counts
            env.image_updates = prev.image_updates
            env.image_counts = prev.image_counts
            env.volume_counts = prev.volume_counts
            env.network_counts = prev.network_counts
            env.docker = prev.docker
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
        # Optional extras: a key without these permissions must not break the host.
        extras = await asyncio.gather(
            self.client.image_counts(env.id),
            self.client.volume_counts(env.id),
            self.client.network_counts(env.id),
            *([] if env.docker else [self.client.docker_info(env.id)]),
            return_exceptions=True,
        )
        for attr, res in zip(("image_counts", "volume_counts", "network_counts", "docker"), extras):
            if isinstance(res, dict):
                setattr(env, attr, res)
            elif isinstance(res, Exception) and not isinstance(res, ArcaneError):
                raise res
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
            self._note_manager_offline()
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
        self._fire_events(prev, data)
        await self._async_cleanup(data)
        self._settle_upgrades(data)
        self._sync_issues(data)
        return data

    # --- events -------------------------------------------------------------

    @callback
    def _fire_events(self, prev: dict[str, EnvData], data: ArcaneData) -> None:
        """Fire bus events for state changes seen between two polls (not on first sight)."""
        if not self.config_entry.options.get(CONF_EVENTS, True):
            return
        for env in data.environments.values():
            old = prev.get(env.id)
            if old is None or not (old.ok and env.ok):
                continue
            base = {"environment": env.name, "environment_id": env.id}
            for name, cont in env.containers.items():
                was = old.containers.get(name)
                if was is None or was.get("state") == cont.get("state"):
                    continue
                code = exit_code(cont)
                self.hass.bus.async_fire(
                    EVENT_CONTAINER_STATE,
                    {
                        **base,
                        "container": name,
                        "state": cont.get("state"),
                        "previous_state": was.get("state"),
                        "exit_code": code,
                        "crashed": was.get("state") == "running" and bool(code),
                        "oom_killed": was.get("state") == "running" and code == 137,
                    },
                )
            for name, proj in env.projects.items():
                was = old.projects.get(name)
                if was is None or was.get("status") == proj.get("status"):
                    continue
                self.hass.bus.async_fire(
                    EVENT_PROJECT_STATE,
                    {**base, "project": name, "status": proj.get("status"),
                     "previous_status": was.get("status")},
                )

    # --- actions on a whole host -------------------------------------------

    async def async_check_updates(self, env_id: str) -> None:
        await guarded(self.client.check_all_image_updates(env_id), "image-updates:check")
        await self.async_request_refresh()

    async def async_update_all(self, env_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Apply every pending update of one host; returns Arcane's counters."""
        env = self.data.environments.get(env_id)
        names = (
            []
            if env is None or dry_run
            else [
                n
                for n, c in env.containers.items()
                if (c.get("updateInfo") or {}).get("hasUpdate") and not c.get("redeployDisabled")
            ]
        )
        for n in names:
            self.mark_upgrading(env_id, n, None, blocking=True)
        try:
            result = await guarded(
                self.client.run_updater(env_id, dry_run=dry_run), "image-updates:check"
            )
        finally:
            for n in names:
                self.unmark_upgrading(env_id, n)
        if not dry_run:
            await self.async_request_refresh()
        return {k: result.get(k) for k in ("checked", "updated", "skipped", "failed")}

    # --- repairs ------------------------------------------------------------

    def _issue_id(self, env_id: str) -> str:
        return f"check_errors_{self.config_entry.entry_id}_{env_id}"

    @callback
    def _sync_issues(self, data: ArcaneData) -> None:
        """Raise a Repair for hosts whose image update check keeps failing."""
        now = time.time()
        failing: dict[str, tuple[EnvData, list[tuple[str, str]]]] = {}
        for env in data.environments.values():
            if not env.ok:
                continue
            errs = [
                (n, str((c.get("updateInfo") or {}).get("error") or ""))
                for n, c in env.containers.items()
            ]
            errs = [(n, e) for n, e in errs if e]
            if errs:
                failing[env.id] = (env, errs)
        for env_id, (env, errs) in failing.items():
            since = self._errors_since.setdefault(env_id, now)
            if now - since < ERROR_ISSUE_AFTER:
                continue
            shown = ", ".join(n for n, _ in errs[:5]) + (" …" if len(errs) > 5 else "")
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id(env_id),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="check_errors",
                translation_placeholders={
                    "host": env.name,
                    "count": str(len(errs)),
                    "containers": shown,
                    "example": errs[0][1][:160],
                },
            )
            self._issues.add(self._issue_id(env_id))
        for env_id in list(self._errors_since):
            if env_id in failing:
                continue
            env = data.environments.get(env_id)
            if env is not None and not env.ok:
                continue  # host unreachable right now: keep what we know
            del self._errors_since[env_id]
            if self._issue_id(env_id) in self._issues:
                ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(env_id))
                self._issues.discard(self._issue_id(env_id))

    @callback
    def async_clear_issues(self) -> None:
        for issue_id in self._issues:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        self._issues.clear()

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
