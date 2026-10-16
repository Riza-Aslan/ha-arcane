# Arcane for Home Assistant

Custom integration for [Arcane](https://getarcane.app), the Docker management UI. It talks to the Arcane REST API (`X-Api-Key`) and polls every 60 seconds.

## Entities

Per configured Arcane environment:

| Platform | What |
|---|---|
| `sensor` | Containers running / stopped / total, projects running / stopped / total, images with updates, image update check errors |
| `switch` | One per **container** (start/stop) and one per **project** (compose up/down) |
| `button` | Restart per container and per project, plus "Check image updates" |
| `update` | One per container. Shows installed/latest version from Arcane's update check. **Install = Arcane redeploy** (pulls the image and recreates the container) |

Containers and projects that appear later get entities automatically; removed ones go `unavailable`.

## Install

**HACS (custom repository):** HACS → ⋮ → Custom repositories → `https://github.com/Riza-Aslan/ha-arcane`, type *Integration* → install → restart Home Assistant.

**Manual:** copy `custom_components/arcane` to `<config>/custom_components/arcane` and restart.

## Setup

1. In Arcane: **Settings → API Keys → Add API Key**. Copy the key, it is shown once.
2. In Home Assistant: **Settings → Devices & services → Add integration → Arcane**.
3. Enter the base URL (e.g. `https://arcane.example.com`), the API key and the environment ID (`0` is the local environment; remote agents have their own IDs, add the integration once per environment).

If the key is revoked or expires, Home Assistant starts a re-authentication flow.

## Notes and limits

- **Permissions:** the key acts as the Arcane user who created it. Switches, buttons and "Install" really start, stop, restart and redeploy things. Use a key of a user with only the rights you want Home Assistant to have.
- **Update entities** only show what Arcane's own update check knows. Press "Check image updates" (or let Arcane's scheduler do it) to refresh.
- Container and project entities are keyed by name. Renaming one creates new entities.
- API endpoints were taken from the Arcane source (v2.x, `/api/openapi.json`), not from a long-running production setup. See the test status below.

## Tested

`pytest` (under a second, no network) runs the integration in a real Home Assistant test harness against a fake Arcane server that mirrors the response shapes from the Arcane source: config flow (good key, bad key, unreachable), entity states, all actions including the streaming `/up`, dynamic entities and re-auth. **Not yet tested against a live Arcane instance**, please open an issue if a field differs on your version.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements_test.txt && .venv/bin/pytest
```

## License

MIT for the integration code. The Arcane logo in `brand/` is taken from the [Arcane project](https://github.com/getarcaneapp/arcane) (BSD 3-Clause, © 2025 Kyle Mendell) and belongs to its authors. This is an unofficial community integration.
