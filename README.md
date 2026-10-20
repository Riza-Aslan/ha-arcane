# Arcane for Home Assistant

[![License](https://img.shields.io/github/license/Riza-Aslan/ha-arcane)](https://github.com/Riza-Aslan/ha-arcane/blob/main/LICENSE)
[![GitHub Sponsors](https://img.shields.io/github/sponsors/Riza-Aslan)](https://github.com/sponsors/Riza-Aslan)

## ❤️ Support

If you find this integration useful, please consider supporting its development:

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-Riza--Aslan-ea4aaa?logo=github-sponsors&logoColor=white)](https://github.com/sponsors/Riza-Aslan)
[![PayPal](https://img.shields.io/badge/PayPal-Donate-00457C?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/rizaaslan)

Custom integration for [Arcane](https://getarcane.app), the Docker management UI. It talks to the Arcane REST API (`X-Api-Key`) and polls every 60 seconds.

## Entities

The integration reads **all environments** (hosts) of your Arcane manager. Every host becomes its own device in Home Assistant, so containers are grouped by host and identically named containers on different hosts do not collide.

| Device | What |
|---|---|
| **Arcane** (the manager) | Button "Clean up missing" |
| **One per host** (e.g. *As-VPS*) | Sensors: containers running / stopped / total, projects running / stopped / total, images with updates, update check errors. Button: "Check image updates" |
| – per container | `switch` start/stop, `button` restart, `update` (installed/latest version, **install = Arcane redeploy**) |
| – per project | `switch` compose up/down, `button` restart |

Entity IDs follow the device name, e.g. `switch.as_vps_container_traefik_vps`.

## Tidying up

Containers and projects come and go. Home Assistant keeps entities of things that vanished unless the integration removes them, so this one does:

- A container, project or host that is gone turns `unavailable` first and is **removed after a grace period** (default 24 h, option *Grace period*, `0` = never). If it comes back in the meantime (redeploy, restart), nothing is removed.
- **A host that is offline never causes removals.** Only items of a host that answered the last poll count as gone.
- Docker's temporary names while recreating a container (`<12 hex>_name`) get no entities at all.
- Button **Clean up missing** removes everything that is verifiably gone right now. Devices without entities are removed with it.
- Option *Hosts* limits the integration to selected hosts (empty = all). Entities of deselected hosts are cleaned up like vanished ones.

## Install

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Riza-Aslan&repository=ha-arcane&category=integration)

**HACS (custom repository):** HACS → ⋮ → Custom repositories → `https://github.com/Riza-Aslan/ha-arcane`, type *Integration* → install → restart Home Assistant.

**Manual:** copy `custom_components/arcane` to `<config>/custom_components/arcane` and restart.

## Setup

1. In Arcane: **Settings → API Keys → Add API Key**. Copy the key, it is shown once.
2. In Home Assistant: **Settings → Devices & services → Add integration → Arcane**.
3. Enter the base URL (e.g. `https://arcane.example.com`) and the API key. All hosts are found automatically; pick or limit them later under *Configure*.

If the key is revoked or expires, Home Assistant starts a re-authentication flow.

## Notes and limits

- **Permissions:** the key acts as the Arcane user who created it. Switches, buttons and "Install" really start, stop, restart and redeploy things. Use a key of a user with only the rights you want Home Assistant to have.
- **Update entities** only show what Arcane's own update check knows. Press "Check image updates" (or let Arcane's scheduler do it) to refresh.
- Container and project entities are keyed by name and host. Renaming one creates new entities; the old ones are cleaned up after the grace period.
- **Upgrading from 0.1.x:** the entry is migrated automatically. Existing entities keep their entity IDs and history; they move to the device of their host. A 0.1.x setup that was limited to one environment stays limited to it.
- Update entities for `latest`-style tags compare digests (shown shortened to 12 characters), since there is no version number.

## Tested

- `pytest` (under a second, no network) runs the integration in a real Home Assistant test harness against a fake Arcane server: config flow (good key, bad key, unreachable), entity states, all actions including the streaming `/up`, dynamic entities, digest updates and re-auth.
- Read path verified against a live **Arcane v2.16.1** (config flow, all sensors, container switch/button/update entity). An API key with only `containers:list`, `projects:list`, `image-updates:read` and `environments:list` is enough to set up and read everything. Add `containers:start|stop|restart|redeploy`, `projects:deploy|down|restart` and `image-updates:check` for the switches, buttons and *Install*.
- Write actions (start/stop/restart/redeploy, project up/down) are covered by the fake server only, not yet against a live instance.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements_test.txt && .venv/bin/pytest
```

## Support

If this saves you time, a sponsorship or donation (see the buttons at the top) keeps it maintained. Bug reports and PRs are just as welcome.

## License

MIT for the integration code. The Arcane logo in `brand/` is taken from the [Arcane project](https://github.com/getarcaneapp/arcane) (BSD 3-Clause, © 2025 Kyle Mendell) and belongs to its authors. This is an unofficial community integration.
