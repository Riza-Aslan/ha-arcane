"""End-to-end tests against the fake Arcane server."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.arcane.api import ArcaneAuthError, ArcaneClient
from custom_components.arcane.const import CONF_API_KEY, CONF_ENVIRONMENT_ID, DOMAIN

from .conftest import KEY


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    return


def _data(server, key=KEY):
    return {CONF_URL: str(server.make_url("")).rstrip("/"), CONF_API_KEY: key, CONF_ENVIRONMENT_ID: "0"}


async def _setup(hass: HomeAssistant, server) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=_data(server), unique_id="x")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_client_auth_error(hass, server):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    client = ArcaneClient(async_get_clientsession(hass), _data(server)[CONF_URL], "wrong")
    with pytest.raises(ArcaneAuthError):
        await client.container_counts()


async def test_config_flow_ok_and_bad_key(hass, server):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    bad = await hass.config_entries.flow.async_configure(result["flow_id"], _data(server, "nope"))
    assert bad["type"] is FlowResultType.FORM and bad["errors"] == {"base": "invalid_auth"}
    ok = await hass.config_entries.flow.async_configure(result["flow_id"], _data(server))
    assert ok["type"] is FlowResultType.CREATE_ENTRY


async def test_config_flow_unreachable(hass, socket_enabled):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    res = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://127.0.0.1:1", CONF_API_KEY: "k", CONF_ENVIRONMENT_ID: "0"}
    )
    assert res["errors"] == {"base": "cannot_connect"}


async def test_entities_and_state(hass, server):
    entry = await _setup(hass, server)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.arcane_containers_running").state == "1"
    assert hass.states.get("sensor.arcane_projects_total").state == "1"
    assert hass.states.get("sensor.arcane_images_with_updates").state == "1"
    assert hass.states.get("switch.arcane_container_web").state == "on"
    assert hass.states.get("switch.arcane_container_db").state == "off"
    assert hass.states.get("switch.arcane_project_shop").state == "on"
    assert hass.states.get("update.arcane_container_web").state == "on"
    assert hass.states.get("update.arcane_container_db").state == "off"


async def test_actions_hit_api(hass, server, state):
    await _setup(hass, server)
    svc = lambda d, s, e: hass.services.async_call(d, s, {"entity_id": e}, blocking=True)  # noqa: E731
    await svc("switch", "turn_off", "switch.arcane_container_web")
    await svc("switch", "turn_on", "switch.arcane_container_db")
    await svc("button", "press", "button.arcane_container_web_restart")
    await svc("switch", "turn_on", "switch.arcane_project_shop")  # streaming /up
    await svc("switch", "turn_off", "switch.arcane_project_shop")
    await svc("button", "press", "button.arcane_project_shop_restart")
    await svc("button", "press", "button.arcane_check_image_updates")
    await svc("update", "install", "update.arcane_container_web")
    assert state["calls"] == [
        ("POST", "/api/environments/0/containers/c1/stop"),
        ("POST", "/api/environments/0/containers/c2/start"),
        ("POST", "/api/environments/0/containers/c1/restart"),
        ("POST", "/api/environments/0/projects/p1/up"),
        ("POST", "/api/environments/0/projects/p1/down"),
        ("POST", "/api/environments/0/projects/p1/restart"),
        ("POST", "/api/environments/0/image-updates/check-all"),
        ("POST", "/api/environments/0/containers/c1/redeploy"),
    ]


async def test_new_container_appears_and_removed_goes_unavailable(hass, server, state):
    entry = await _setup(hass, server)
    state["containers"].append(
        {"id": "c3", "names": ["/new"], "image": "redis", "state": "running", "status": "Up"}
    )
    state["containers"].pop(1)  # db removed
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("switch.arcane_container_new").state == "on"
    assert hass.states.get("switch.arcane_container_db").state == "unavailable"


async def test_reauth_on_key_revoked(hass, server):
    entry = await _setup(hass, server)
    entry.runtime_data.client._headers["X-Api-Key"] = "revoked"  # server now rejects the key
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress())


async def test_digest_update_shows_digests(hass, server, state):
    state["containers"][0]["updateInfo"] = {
        "hasUpdate": True, "updateType": "digest", "currentVersion": "latest",
        "currentDigest": "sha256:aaaaaaaaaaaaffff", "latestDigest": "sha256:bbbbbbbbbbbbffff", "error": "",
    }
    await _setup(hass, server)
    s = hass.states.get("update.arcane_container_web")
    assert s.state == "on"
    assert s.attributes["installed_version"] == "aaaaaaaaaaaa"
    assert s.attributes["latest_version"] == "bbbbbbbbbbbb"
