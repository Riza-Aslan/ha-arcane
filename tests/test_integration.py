"""End-to-end tests against the fake Arcane server."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.arcane import migrate_unique_id
from homeassistant.helpers import issue_registry as ir
from custom_components.arcane.api import ArcaneAuthError, ArcaneClient
from custom_components.arcane.const import (
    CONF_API_KEY,
    CONF_ENVIRONMENTS,
    CONF_STALE_HOURS,
    DOMAIN,
)

from .conftest import KEY


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    return


def _data(server, key=KEY):
    return {CONF_URL: str(server.make_url("")).rstrip("/"), CONF_API_KEY: key}


async def _setup(hass: HomeAssistant, server, options=None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data=_data(server), unique_id="x", version=2, options=options or {}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _ids(hass, entry, domain):
    reg = er.async_get(hass)
    return {e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id) if e.domain == domain}


async def _refresh(hass, entry):
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_client_auth_error(hass, server):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    client = ArcaneClient(async_get_clientsession(hass), _data(server)[CONF_URL], "wrong")
    with pytest.raises(ArcaneAuthError):
        await client.environments()


async def test_config_flow_ok_and_bad_key(hass, server):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], _data(server, "nope"))
    assert bad["type"] is FlowResultType.FORM and bad["errors"] == {"base": "invalid_auth"}
    ok = await hass.config_entries.flow.async_configure(result["flow_id"], _data(server))
    assert ok["type"] is FlowResultType.CREATE_ENTRY


async def test_config_flow_unreachable(hass, socket_enabled):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://127.0.0.1:1", CONF_API_KEY: "k"}
    )
    assert res["errors"] == {"base": "cannot_connect"}


async def test_one_device_per_host_and_same_names_do_not_clash(hass, server):
    entry = await _setup(hass, server)
    assert entry.state is ConfigEntryState.LOADED
    dev = dr.async_get(hass)
    names = {d.name for d in dr.async_entries_for_config_entry(dev, entry.entry_id)}
    assert names == {"Arcane", "Local Docker", "As-VPS"}
    # both hosts have a container called "web": two separate switches, each on its own host
    switches = _ids(hass, entry, "switch")
    assert len([s for s in switches if "container_web" in s and "auto_update" not in s]) == 2
    reg = er.async_get(hass)
    hosts = set()
    for sid in switches:
        e = reg.async_get(sid)
        if e.unique_id.endswith("|container|web"):
            hosts.add(dev.async_get(e.device_id).name)
    assert hosts == {"Local Docker", "As-VPS"}
    # recreate artefact "<12hex>_web" got no entities
    assert not [s for s in switches if "8e390ad593fd" in s]
    assert hass.states.get("switch.local_docker_container_db").state == "off"
    assert hass.states.get("sensor.local_docker_containers_total").state == "2"
    assert hass.states.get("sensor.as_vps_containers_total").state == "3"


async def test_actions_hit_the_right_host(hass, server, state):
    await _setup(hass, server)
    svc = lambda d, s, e: hass.services.async_call(d, s, {"entity_id": e}, blocking=True)  # noqa: E731
    await svc("switch", "turn_off", "switch.local_docker_container_web")
    await svc("switch", "turn_off", "switch.as_vps_container_web")
    await svc("button", "press", "button.as_vps_container_web_restart")
    await svc("switch", "turn_on", "switch.local_docker_project_shop")
    await svc("switch", "turn_off", "switch.local_docker_project_shop")
    await svc("button", "press", "button.local_docker_project_shop_restart")
    await svc("button", "press", "button.as_vps_check_image_updates")
    await svc("update", "install", "update.local_docker_container_web")
    assert state["calls"] == [
        ("POST", "/api/environments/0/containers/c1/stop"),
        ("POST", "/api/environments/h2/containers/v1/stop"),
        ("POST", "/api/environments/h2/containers/v1/restart"),
        ("POST", "/api/environments/0/projects/p1/up"),
        ("POST", "/api/environments/0/projects/p1/down"),
        ("POST", "/api/environments/0/projects/p1/restart"),
        ("POST", "/api/environments/h2/image-updates/check-all"),
        ("POST", "/api/environments/0/containers/c1/redeploy"),
    ]


async def test_gone_container_removed_only_after_grace_period(hass, server, state, monkeypatch):
    import custom_components.arcane.coordinator as co

    entry = await _setup(hass, server)  # default grace: 24 h
    state["hosts"]["0"]["containers"].pop(1)  # db removed on host 0
    await _refresh(hass, entry)
    # still there, unavailable, nothing removed yet
    assert hass.states.get("switch.local_docker_container_db").state == "unavailable"
    assert hass.states.get("update.local_docker_container_db").state == "unavailable"
    # 25 hours later
    real = co.time.time
    monkeypatch.setattr(co.time, "time", lambda: real() + 25 * 3600)
    await _refresh(hass, entry)
    assert hass.states.get("switch.local_docker_container_db") is None
    assert hass.states.get("button.local_docker_container_db_restart") is None
    assert hass.states.get("switch.local_docker_container_web").state == "on"  # untouched


async def test_returning_container_is_not_removed(hass, server, state, monkeypatch):
    import custom_components.arcane.coordinator as co

    entry = await _setup(hass, server)
    saved = state["hosts"]["0"]["containers"].pop(1)
    await _refresh(hass, entry)  # marked missing
    state["hosts"]["0"]["containers"].append(saved)  # redeploy finished
    await _refresh(hass, entry)
    real = co.time.time
    monkeypatch.setattr(co.time, "time", lambda: real() + 48 * 3600)
    await _refresh(hass, entry)
    assert hass.states.get("switch.local_docker_container_db").state == "off"


async def test_offline_host_never_triggers_removal(hass, server, state, monkeypatch):
    import custom_components.arcane.coordinator as co

    entry = await _setup(hass, server, {CONF_STALE_HOURS: 1})
    state["hosts"]["h2"]["status"] = "offline"
    real = co.time.time
    monkeypatch.setattr(co.time, "time", lambda: real() + 10 * 3600)
    await _refresh(hass, entry)
    await _refresh(hass, entry)
    assert hass.states.get("switch.as_vps_container_web").state == "unavailable"
    state["hosts"]["h2"]["status"] = "online"
    await _refresh(hass, entry)
    assert hass.states.get("switch.as_vps_container_web").state == "on"


async def test_failing_host_endpoint_is_not_a_removal(hass, server, state, monkeypatch):
    import custom_components.arcane.coordinator as co

    entry = await _setup(hass, server, {CONF_STALE_HOURS: 1})
    state["hosts"]["h2"]["fail"] = True
    real = co.time.time
    monkeypatch.setattr(co.time, "time", lambda: real() + 10 * 3600)
    await _refresh(hass, entry)
    assert hass.states.get("switch.as_vps_container_web").state == "unavailable"
    assert hass.states.get("sensor.local_docker_containers_total").state == "2"  # other host fine


async def test_cleanup_button_removes_immediately_and_stale_hours_zero_disables(hass, server, state):
    entry = await _setup(hass, server, {CONF_STALE_HOURS: 0})
    state["hosts"]["0"]["containers"].pop(1)
    await _refresh(hass, entry)
    await _refresh(hass, entry)
    assert hass.states.get("switch.local_docker_container_db").state == "unavailable"  # 0 = never auto
    await hass.services.async_call("button", "press", {"entity_id": "button.arcane_clean_up_missing"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.local_docker_container_db") is None
    assert hass.states.get("switch.local_docker_container_web").state == "on"


async def test_removed_host_is_cleaned_with_its_device(hass, server, state):
    entry = await _setup(hass, server)
    del state["hosts"]["h2"]
    await _refresh(hass, entry)
    await hass.services.async_call("button", "press", {"entity_id": "button.arcane_clean_up_missing"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("switch.as_vps_container_web") is None
    names = {d.name for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    assert names == {"Arcane", "Local Docker"}


async def test_environment_filter_option(hass, server):
    entry = await _setup(hass, server, {CONF_ENVIRONMENTS: ["h2"]})
    assert hass.states.get("sensor.as_vps_containers_total") is not None
    assert hass.states.get("sensor.local_docker_containers_total") is None


async def test_new_container_appears(hass, server, state):
    entry = await _setup(hass, server)
    state["hosts"]["h2"]["containers"].append(
        {"id": "v2", "names": ["/new"], "image": "redis", "state": "running", "status": "Up"})
    await _refresh(hass, entry)
    assert hass.states.get("switch.as_vps_container_new").state == "on"


async def test_digest_update_shows_digests(hass, server, state):
    state["hosts"]["0"]["containers"][0]["updateInfo"] = {
        "hasUpdate": True, "updateType": "digest", "currentVersion": "latest",
        "currentDigest": "sha256:aaaaaaaaaaaaffff", "latestDigest": "sha256:bbbbbbbbbbbbffff", "error": "",
    }
    await _setup(hass, server)
    s = hass.states.get("update.local_docker_container_web")
    assert s.state == "on"
    assert s.attributes["installed_version"] == "aaaaaaaaaaaa"
    assert s.attributes["latest_version"] == "bbbbbbbbbbbb"


async def test_reauth_on_key_revoked(hass, server):
    entry = await _setup(hass, server)
    entry.runtime_data.client._headers["X-Api-Key"] = "revoked"
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_migration_keeps_entity_ids(hass, server):
    """A v1 entry (single environment) keeps its entity_ids; unique_ids move to the v2 scheme."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, unique_id="old",
        data={**_data(server), "environment_id": "0"},
    )
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    old = {
        ("switch", "container_web"): f"{entry.entry_id}_container_web",
        ("button", "container_web_restart"): f"{entry.entry_id}_container_restart_web",
        ("update", "container_web"): f"{entry.entry_id}_container_update_web",
        ("sensor", "containers_running"): f"{entry.entry_id}_containers_running",
        ("button", "check_image_updates"): f"{entry.entry_id}_check_image_updates",
    }
    made = {}
    for (dom, name), uid in old.items():
        made[(dom, name)] = reg.async_get_or_create(
            dom, DOMAIN, uid, config_entry=entry, suggested_object_id=f"arcane_{name}").entity_id
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.version == 2 and "environment_id" not in entry.data
    # same entity_ids are still registered and now live: no duplicates created
    for eid in made.values():
        assert reg.async_get(eid) is not None
    assert hass.states.get(made[("switch", "container_web")]).state == "on"
    uid = reg.async_get(made[("switch", "container_web")]).unique_id
    assert uid == f"{entry.entry_id}|0|container|web"


def test_migrate_unique_id_mapping():
    e = "E"
    assert migrate_unique_id(e, "0", "E_container_web") == "E|0|container|web"
    assert migrate_unique_id(e, "0", "E_container_restart_web") == "E|0|container_restart|web"
    assert migrate_unique_id(e, "0", "E_container_update_web") == "E|0|container_update|web"
    assert migrate_unique_id(e, "0", "E_project_restart_shop") == "E|0|project_restart|shop"
    assert migrate_unique_id(e, "0", "E_project_shop") == "E|0|project|shop"
    assert migrate_unique_id(e, "0", "E_containers_running") == "E|0|containers_running"
    assert migrate_unique_id(e, "0", "E_check_image_updates") == "E|0|check_image_updates"
    assert migrate_unique_id(e, "0", "other") is None


async def test_update_title_names_the_container(hass, server):
    """The update panel shows host as headline; the container must be visible in `title`."""
    await _setup(hass, server)
    assert hass.states.get("update.local_docker_container_web").attributes["title"] == "web (nginx:latest)"
    assert hass.states.get("update.as_vps_container_web").attributes["title"] == "web (caddy)"


async def test_arcane_agent_uses_system_upgrade_not_redeploy(hass, server, state):
    """Arcane refuses to redeploy its own agent (redeployDisabled); use the self-upgrade."""
    await _setup(hass, server)
    ent = "update.as_vps_container_arcane_edge_agent"
    assert hass.states.get(ent).state == "on"
    state["calls"].clear()
    await hass.services.async_call("update", "install", {"entity_id": ent}, blocking=True)
    assert state["calls"] == [("POST", "/api/environments/h2/system/upgrade")]


AGENT = "update.as_vps_container_arcane_edge_agent"


async def test_agent_upgrade_shows_progress_until_container_is_replaced(hass, server, state):
    entry = await _setup(hass, server)
    assert hass.states.get(AGENT).attributes["in_progress"] is False
    await hass.services.async_call("update", "install", {"entity_id": AGENT}, blocking=True)
    # the call returned at once, but the update is still running
    assert hass.states.get(AGENT).attributes["in_progress"] is True
    assert entry.runtime_data.is_upgrading("h2", "arcane-edge-agent")
    # host goes offline while the agent restarts: entity stays visible and in progress
    state["hosts"]["h2"]["status"] = "offline"
    await _refresh(hass, entry)
    s = hass.states.get(AGENT)
    assert s.state != "unavailable" and s.attributes["in_progress"] is True
    # host is back with a recreated container: progress ends
    state["hosts"]["h2"]["status"] = "online"
    state["hosts"]["h2"]["containers"][1]["id"] = "ag-new"
    state["hosts"]["h2"]["containers"][1]["updateInfo"]["hasUpdate"] = False
    await _refresh(hass, entry)
    assert hass.states.get(AGENT).attributes["in_progress"] is False
    assert not entry.runtime_data.is_upgrading("h2", "arcane-edge-agent")


async def test_progress_ends_when_host_returns_even_with_same_container_id(hass, server, state):
    entry = await _setup(hass, server)
    await hass.services.async_call("update", "install", {"entity_id": AGENT}, blocking=True)
    state["hosts"]["h2"]["status"] = "offline"
    await _refresh(hass, entry)
    state["hosts"]["h2"]["status"] = "online"
    await _refresh(hass, entry)
    assert hass.states.get(AGENT).attributes["in_progress"] is False


async def test_progress_times_out(hass, server, state, monkeypatch):
    import custom_components.arcane.coordinator as co

    entry = await _setup(hass, server)
    await hass.services.async_call("update", "install", {"entity_id": AGENT}, blocking=True)
    real = co.time.time
    monkeypatch.setattr(co.time, "time", lambda: real() + co.UPGRADE_TIMEOUT + 5)
    await _refresh(hass, entry)
    assert hass.states.get(AGENT).attributes["in_progress"] is False


async def test_agent_upgrade_conflict_is_not_an_error(hass, server, state):
    """409 = upgrade already running (e.g. a second click): keep showing progress."""
    state["upgrade_status"] = 409
    entry = await _setup(hass, server)
    await hass.services.async_call("update", "install", {"entity_id": AGENT}, blocking=True)
    assert hass.states.get(AGENT).attributes["in_progress"] is True


async def test_agent_upgrade_failure_clears_progress_and_explains(hass, server, state):
    from homeassistant.exceptions import HomeAssistantError

    state["upgrade_status"] = 403
    await _setup(hass, server)
    with pytest.raises(HomeAssistantError, match="system:upgrade"):
        await hass.services.async_call("update", "install", {"entity_id": AGENT}, blocking=True)
    assert hass.states.get(AGENT).attributes["in_progress"] is False
    state["upgrade_status"] = 500
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("update", "install", {"entity_id": AGENT}, blocking=True)
    assert hass.states.get(AGENT).attributes["in_progress"] is False


async def test_normal_redeploy_clears_progress_afterwards(hass, server, state):
    await _setup(hass, server)
    await hass.services.async_call(
        "update", "install", {"entity_id": "update.local_docker_container_web"}, blocking=True)
    assert hass.states.get("update.local_docker_container_web").attributes["in_progress"] is False


def _device(hass, entry, ident):
    """Find the device of this entry by identifier (works across HA versions)."""
    for dev in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, ident) in dev.identifiers:
            return dev
    raise AssertionError(f"no device {ident}")


async def _enable_entity(hass, entry, entity_id):
    """Enable an entity that is disabled by default and reload the entry."""
    reg = er.async_get(hass)
    assert reg.async_get(entity_id) is not None, f"{entity_id} not registered"
    reg.async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_host_online_sensor(hass, server, state):
    entry = await _setup(hass, server)
    assert hass.states.get("binary_sensor.as_vps_online").state == "on"
    state["hosts"]["h2"]["status"] = "offline"
    await _refresh(hass, entry)
    s = hass.states.get("binary_sensor.as_vps_online")
    assert s.state == "off" and s.attributes["status"] == "offline"  # still available
    assert hass.states.get("switch.as_vps_container_web").state == "unavailable"


async def test_update_all_button_and_service(hass, server, state):
    entry = await _setup(hass, server)
    state["calls"].clear()
    await hass.services.async_call("button", "press", {"entity_id": "button.as_vps_update_all"}, blocking=True)
    await hass.async_block_till_done()
    assert state["calls"] == [("POST", "/api/environments/h2/updater/run", "true", {"dryRun": False})]
    dev = _device(hass, entry, f"{entry.entry_id}|h2")
    state["calls"].clear()
    out = await hass.services.async_call(
        DOMAIN, "update_all", {"device_id": dev.id, "dry_run": True}, blocking=True, return_response=True)
    assert out == {"hosts": {"As-VPS": {"checked": 3, "updated": 2, "skipped": 1, "failed": 0}}}
    assert state["calls"] == [("POST", "/api/environments/h2/updater/run", "true", {"dryRun": True})]


async def test_update_all_marks_progress_and_reports_errors(hass, server, state):
    from homeassistant.exceptions import HomeAssistantError

    entry = await _setup(hass, server)
    dev = _device(hass, entry, f"{entry.entry_id}|0")
    state["updater_status"] = 403
    with pytest.raises(HomeAssistantError, match="image-updates:check"):
        await hass.services.async_call(DOMAIN, "update_all", {"device_id": dev.id}, blocking=True)
    assert hass.states.get("update.local_docker_container_web").attributes["in_progress"] is False


async def test_check_updates_service(hass, server, state):
    entry = await _setup(hass, server)
    dev = _device(hass, entry, f"{entry.entry_id}|h2")
    state["calls"].clear()
    await hass.services.async_call(DOMAIN, "check_updates", {"device_id": dev.id}, blocking=True)
    assert state["calls"] == [("POST", "/api/environments/h2/image-updates/check-all")]


async def test_services_reject_the_manager_device(hass, server):
    from homeassistant.exceptions import ServiceValidationError

    entry = await _setup(hass, server)
    dev = _device(hass, entry, entry.entry_id)
    with pytest.raises(ServiceValidationError, match="manager"):
        await hass.services.async_call(DOMAIN, "check_updates", {"device_id": dev.id}, blocking=True)


async def test_auto_update_switch(hass, server, state):
    entry = await _setup(hass, server)
    ent = "switch.as_vps_container_web_auto_update"
    await _enable_entity(hass, entry, ent)
    assert hass.states.get(ent).state == "on"
    state["calls"].clear()
    await hass.services.async_call("switch", "turn_off", {"entity_id": ent}, blocking=True)
    assert state["calls"] == [("PUT", "/api/environments/h2/containers/v1/auto-update", {"enabled": False})]


async def test_project_update_entity(hass, server, state):
    entry = await _setup(hass, server)
    ent = "update.local_docker_project_shop"
    await _enable_entity(hass, entry, ent)
    s = hass.states.get(ent)
    assert s.state == "on" and s.attributes["latest_version"] == "2 new images"
    assert s.attributes["services_with_updates"] == ["app"]
    state["calls"].clear()
    await hass.services.async_call("update", "install", {"entity_id": ent}, blocking=True)
    assert state["calls"] == [("POST", "/api/environments/0/projects/p1/redeploy")]
    assert hass.states.get(ent).attributes["in_progress"] is False


async def test_repair_issue_only_for_persistent_check_errors(hass, server, state, monkeypatch):
    import custom_components.arcane.coordinator as co

    entry = await _setup(hass, server)
    issues = ir.async_get(hass)
    assert not [i for i in issues.issues.values() if i[0] == DOMAIN]  # not right away
    real = co.time.time
    monkeypatch.setattr(co.time, "time", lambda: real() + co.ERROR_ISSUE_AFTER + 10)
    await _refresh(hass, entry)
    found = [i for (d, _), i in issues.issues.items() if d == DOMAIN]
    assert len(found) == 1 and found[0].translation_placeholders["host"] == "As-VPS"
    assert found[0].translation_placeholders["containers"] == "web"
    # fixed: the issue goes away
    state["hosts"]["h2"]["containers"][0]["updateInfo"] = {"hasUpdate": False, "error": ""}
    await _refresh(hass, entry)
    assert not [i for (d, _), i in issues.issues.items() if d == DOMAIN]


async def test_reconfigure_changes_key_and_keeps_it_when_left_empty(hass, server):
    entry = await _setup(hass, server)
    res = await entry.start_reconfigure_flow(hass)
    assert res["type"] is FlowResultType.FORM and res["step_id"] == "reconfigure"
    bad = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_URL: _data(server)[CONF_URL], CONF_API_KEY: "nope"})
    assert bad["errors"] == {"base": "invalid_auth"}
    done = await hass.config_entries.flow.async_configure(
        res["flow_id"], {CONF_URL: _data(server)[CONF_URL], CONF_API_KEY: ""})
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "reconfigure_successful"
    assert entry.data[CONF_API_KEY] == KEY
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_host_devices_hang_below_the_manager_without_deprecation(hass, server, caplog):
    entry = await _setup(hass, server)
    manager = _device(hass, entry, entry.entry_id)
    for ident in ("0", "h2"):
        host = _device(hass, entry, f"{entry.entry_id}|{ident}")
        assert host.via_device_id == manager.id
    assert "via_device" not in caplog.text  # no deprecation warning from HA


def _find(hass, entry, suffix):
    reg = er.async_get(hass)
    for e in er.async_entries_for_config_entry(reg, entry.entry_id):
        if e.unique_id.endswith(suffix):
            return e.entity_id
    raise AssertionError(f"no entity ...{suffix}")


async def test_resource_sensors(hass, server):
    entry = await _setup(hass, server)
    assert hass.states.get(_find(hass, entry, "|0|images_total")).state == "11"
    assert hass.states.get(_find(hass, entry, "|0|images_unused")).state == "6"
    assert hass.states.get(_find(hass, entry, "|0|volumes_unused")).state == "7"
    assert hass.states.get(_find(hass, entry, "|0|networks_total")).state == "8"
    assert float(hass.states.get(_find(hass, entry, "|0|image_storage")).state) == pytest.approx(2.13, abs=0.01)
    assert hass.states.get(_find(hass, entry, "|0|containers_unhealthy")).state == "0"


async def test_health_binary_sensor_only_for_containers_with_a_health_check(hass, server, state):
    entry = await _setup(hass, server)
    ok = _find(hass, entry, "|0|container_healthy|web")
    assert hass.states.get(ok).state == "off"
    reg = er.async_get(hass)
    assert not [e for e in er.async_entries_for_config_entry(reg, entry.entry_id) if e.unique_id.endswith("|container_healthy|db")]
    state["hosts"]["0"]["containers"][0]["status"] = "Up 2h (unhealthy)"
    await _refresh(hass, entry)
    assert hass.states.get(ok).state == "on"
    assert hass.states.get(_find(hass, entry, "|0|containers_unhealthy")).state == "1"


async def test_extras_failing_do_not_break_the_host(hass, server, state, monkeypatch):
    from custom_components.arcane.api import ArcaneApiError, ArcaneClient

    async def boom(self, env_id):
        raise ArcaneApiError("HTTP 403", 403)

    monkeypatch.setattr(ArcaneClient, "volume_counts", boom)
    entry = await _setup(hass, server)
    assert hass.states.get("binary_sensor.local_docker_online").state == "on"
    assert hass.states.get(_find(hass, entry, "|0|images_total")).state == "11"
    assert hass.states.get(_find(hass, entry, "|0|volumes_total")).state in ("unknown", "unavailable")


async def test_event_when_a_container_crashes(hass, server, state):
    entry = await _setup(hass, server)
    events = async_capture_events(hass, "arcane_container_state_changed")
    c = state["hosts"]["0"]["containers"][0]
    c["state"], c["status"] = "exited", "Exited (137) 3 seconds ago"
    await _refresh(hass, entry)
    assert len(events) == 1
    d = events[0].data
    assert (d["container"], d["state"], d["previous_state"], d["exit_code"]) == ("web", "exited", "running", 137)
    assert d["crashed"] is True and d["oom_killed"] is True and d["environment"] == "Local Docker"
    # a clean stop (exit 0) is not a crash
    c["state"], c["status"] = "running", "Up 1 second"
    await _refresh(hass, entry)
    c["state"], c["status"] = "exited", "Exited (0) 1 second ago"
    await _refresh(hass, entry)
    assert events[-1].data["crashed"] is False


async def test_no_events_on_first_load_or_when_host_was_offline(hass, server, state):
    events = async_capture_events(hass, "arcane_container_state_changed")
    entry = await _setup(hass, server)
    assert events == []
    state["hosts"]["h2"]["status"] = "offline"
    await _refresh(hass, entry)
    state["hosts"]["h2"]["status"] = "online"
    state["hosts"]["h2"]["containers"][0]["state"] = "exited"
    await _refresh(hass, entry)
    assert events == []  # came back from offline: no comparison possible


async def test_events_can_be_switched_off_and_interval_is_configurable(hass, server, state):
    entry = await _setup(hass, server)
    hass.config_entries.async_update_entry(entry, options={"events": False, "scan_interval": 120})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.update_interval.total_seconds() == 120
    events = async_capture_events(hass, "arcane_container_state_changed")
    state["hosts"]["0"]["containers"][0]["state"] = "exited"
    await _refresh(hass, entry)
    assert events == []


async def test_diagnostics_contain_no_names_or_secrets(hass, server):
    from custom_components.arcane.diagnostics import async_get_config_entry_diagnostics

    entry = await _setup(hass, server)
    out = str(await async_get_config_entry_diagnostics(hass, entry))
    assert KEY not in out and "web" not in out and "127.0.0.1" not in out
    assert "images_total" not in out and "'containers': 2" in out


async def test_prune_images_only_sends_image_mode_and_reports(hass, server, state):
    entry = await _setup(hass, server)
    btn = _find(hass, entry, "|h2|prune_images")
    reg = er.async_get(hass)
    assert reg.async_get(btn).disabled_by is not None  # off by default
    reg.async_update_entity(btn, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state["calls"].clear()
    await hass.services.async_call("button", "press", {"entity_id": btn}, blocking=True)
    assert state["calls"] == [("POST", "/api/environments/h2/images/prune", {"mode": "all"})]
    dev = _device(hass, entry, f"{entry.entry_id}|h2")
    out = await hass.services.async_call(DOMAIN, "prune_images", {"device_id": dev.id}, blocking=True, return_response=True)
    assert out == {"hosts": {"As-VPS": {"images_deleted": 3, "space_reclaimed": 123456}}}


async def test_host_stats_only_fetched_when_a_stat_entity_is_enabled(hass, server, state):
    entry = await _setup(hass, server)
    assert not [c for c in state["calls"] if c[0] == "WS"]  # all stat sensors are off by default
    reg = er.async_get(hass)
    for key in ("cpu_usage", "memory_usage", "disk_usage"):
        reg.async_update_entity(_find(hass, entry, f"|h2|{key}"), disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert [c for c in state["calls"] if c[0] == "WS"] == [("WS", "/api/environments/h2/ws/system/stats")]  # h2 only
    assert float(hass.states.get(_find(hass, entry, "|h2|cpu_usage")).state) == pytest.approx(38.2)
    assert float(hass.states.get(_find(hass, entry, "|h2|memory_usage")).state) == 50.0
    assert float(hass.states.get(_find(hass, entry, "|h2|disk_usage")).state) == 25.0


async def test_read_only_blocks_every_write_but_not_reads_or_update_checks(hass, server, state):
    from homeassistant.exceptions import HomeAssistantError

    entry = await _setup(hass, server)
    hass.config_entries.async_update_entry(entry, options={"read_only": True})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state["calls"].clear()
    with pytest.raises(HomeAssistantError, match="read only"):
        await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.as_vps_container_web"}, blocking=True)
    # "Update all" runs in the background: it must not raise, but it must not write either
    await hass.services.async_call("button", "press", {"entity_id": "button.as_vps_update_all"}, blocking=True)
    await hass.async_block_till_done()
    with pytest.raises(HomeAssistantError, match="read only"):
        await hass.services.async_call(DOMAIN, "prune_images", {"device_id": _device(hass, entry, f"{entry.entry_id}|h2").id}, blocking=True)
    assert [c for c in state["calls"] if c[0] != "GET"] == []
    await hass.services.async_call("button", "press", {"entity_id": "button.as_vps_check_image_updates"}, blocking=True)
    assert [c[1] for c in state["calls"] if c[0] == "POST"] == ["/api/environments/h2/image-updates/check-all"]


async def test_container_state_and_project_services_sensors(hass, server):
    entry = await _setup(hass, server)
    reg = er.async_get(hass)
    for suffix in ("|0|container_state|web", "|0|project_services|shop"):
        reg.async_update_entity(_find(hass, entry, suffix), disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(_find(hass, entry, "|0|container_state|web")).state == "running"
    reg.async_update_entity(_find(hass, entry, "|0|docker_version"), disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(_find(hass, entry, "|0|docker_version")).state == "28.1.1"


async def test_health_change_fires_an_event(hass, server, state):
    entry = await _setup(hass, server)
    events = async_capture_events(hass, "arcane_container_health_changed")
    state["hosts"]["0"]["containers"][0]["status"] = "Up 2h (unhealthy)"
    await _refresh(hass, entry)
    assert [(e.data["container"], e.data["health"], e.data["previous_health"]) for e in events] == [("web", "unhealthy", "healthy")]
