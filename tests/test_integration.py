"""End-to-end tests against the fake Arcane server."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane import migrate_unique_id
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
    assert len([s for s in switches if "container_web" in s]) == 2
    reg = er.async_get(hass)
    hosts = set()
    for sid in switches:
        e = reg.async_get(sid)
        if "web" in e.unique_id.split("|")[-1]:
            hosts.add(dev.async_get(e.device_id).name)
    assert hosts == {"Local Docker", "As-VPS"}
    # recreate artefact "<12hex>_web" got no entities
    assert not [s for s in switches if "8e390ad593fd" in s]
    assert hass.states.get("switch.local_docker_container_db").state == "off"
    assert hass.states.get("sensor.local_docker_containers_total").state == "2"
    assert hass.states.get("sensor.as_vps_containers_total").state == "2"


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
