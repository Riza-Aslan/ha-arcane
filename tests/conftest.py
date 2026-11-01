"""Test fixtures: a fake Arcane server with several environments (hosts)."""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

pytest_plugins = "pytest_homeassistant_custom_component"

KEY = "arc_test_key"


def _env(data):
    return web.json_response({"success": True, "data": data})


def _page(items):
    return web.json_response(
        {
            "success": True,
            "data": items,
            "pagination": {"totalItems": len(items), "totalPages": 1, "currentPage": 1, "itemsPerPage": 100},
        }
    )


def make_app(state):
    calls = state["calls"]

    @web.middleware
    async def auth(request, handler):
        if request.path not in ("/api/health", "/api/app-version") and request.headers.get("X-Api-Key") != KEY:
            return web.json_response({"success": False}, status=401)
        return await handler(request)

    app = web.Application(middlewares=[auth])
    r = app.router

    def get(path, fn):
        async def h(request):
            return fn(request)

        r.add_get(path, h)

    def host(request):
        h = state["hosts"].get(request.match_info["eid"])
        if h is None or h.get("fail"):
            raise web.HTTPInternalServerError()
        return h

    get("/api/health", lambda _r: web.json_response({"status": "UP"}))
    get("/api/app-version", lambda _r: _env({"currentVersion": "2.10.0", "displayVersion": "v2.10.0"}))
    get("/api/environments", lambda _r: _page(
        [{"id": i, "name": h["name"], "status": h.get("status", "online"), "enabled": True, "isEdge": i != "0"}
         for i, h in state["hosts"].items()]))
    get("/api/environments/{eid}/containers/counts", lambda q: _env({
        "runningContainers": sum(c["state"] == "running" for c in host(q)["containers"]),
        "stoppedContainers": sum(c["state"] != "running" for c in host(q)["containers"]),
        "totalContainers": len(host(q)["containers"])}))
    get("/api/environments/{eid}/projects/counts", lambda q: _env({
        "runningProjects": len(host(q)["projects"]), "stoppedProjects": 0, "totalProjects": len(host(q)["projects"])}))
    get("/api/environments/{eid}/image-updates/summary", lambda q: _env(
        {"totalImages": 2, "imagesWithUpdates": 1, "digestUpdates": 1, "errorsCount": 0}))
    get("/api/environments/{eid}/containers", lambda q: _page(host(q)["containers"]))
    get("/api/environments/{eid}/projects", lambda q: _page(host(q)["projects"]))

    async def action(request):
        calls.append((request.method, request.path))
        return _env({"message": "ok"})

    async def up(request):
        calls.append((request.method, request.path))
        resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await resp.prepare(request)
        for line in ({"activityId": "a1"}, {"log": "pulling"}, {"done": True}):
            await resp.write((json.dumps(line) + "\n").encode())
        return resp

    for p in ("start", "stop", "restart", "redeploy"):
        r.add_post(f"/api/environments/{{eid}}/containers/{{cid}}/{p}", action)
    r.add_post("/api/environments/{eid}/projects/{pid}/up", up)
    r.add_post("/api/environments/{eid}/projects/{pid}/down", action)
    r.add_post("/api/environments/{eid}/projects/{pid}/restart", action)
    r.add_post("/api/environments/{eid}/image-updates/check-all", action)
    async def upgrade(request):
        calls.append((request.method, request.path))
        if state.get("upgrade_status"):
            return web.json_response({"title": "x", "detail": "boom"}, status=state["upgrade_status"])
        return _env({"message": "started"})

    r.add_post("/api/environments/{eid}/system/upgrade", upgrade)

    async def stats_ws(request):
        calls.append(("WS", request.path))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(json.dumps({"cpuUsage": 38.2, "memoryUsage": 500, "memoryTotal": 1000,
                                      "diskUsage": 25, "diskTotal": 100}))
        await ws.close()
        return ws

    async def prune(request):
        calls.append((request.method, request.path, await request.json()))
        return _env({"imagesDeleted": ["a", "b", "c"], "spaceReclaimed": 123456})

    r.add_get("/api/environments/{eid}/ws/system/stats", stats_ws)
    r.add_post("/api/environments/{eid}/images/prune", prune)
    get("/api/environments/{eid}/images/counts", lambda q: _env({
        "imagesInuse": 5, "imagesUnused": 6, "totalImages": 11, "totalImageSize": 2_134_602_826}))
    get("/api/environments/{eid}/volumes/counts", lambda q: _env({"inuse": 5, "unused": 7, "total": 12}))
    get("/api/environments/{eid}/networks/counts", lambda q: _env({"inuse": 8, "unused": 0, "total": 8}))
    get("/api/environments/{eid}/system/docker/info", lambda q: web.json_response({"ServerVersion": "28.1.1", "Containers": 6}))

    async def autoupdate(request):
        calls.append((request.method, request.path, await request.json()))
        return _env({"message": "ok"})

    async def run_updater(request):
        calls.append((request.method, request.path, request.query.get("automatic"), await request.json()))
        if state.get("updater_status"):
            return web.json_response({"title": "x"}, status=state["updater_status"])
        return _env({"checked": 3, "updated": 2, "skipped": 1, "failed": 0})

    r.add_put("/api/environments/{eid}/containers/{cid}/auto-update", autoupdate)
    r.add_post("/api/environments/{eid}/updater/run", run_updater)
    r.add_post("/api/environments/{eid}/projects/{pid}/redeploy", up)
    return app


@pytest.fixture
def state():
    return {
        "calls": [],
        "hosts": {
            "0": {
                "name": "Local Docker",
                "containers": [
                    {"id": "c1", "names": ["/web"], "image": "nginx:latest", "state": "running", "status": "Up 2h (healthy)",
                     "updateInfo": {"hasUpdate": True, "currentVersion": "1.27", "latestVersion": "1.28", "updateType": "tag", "error": ""}},
                    {"id": "c2", "names": ["/db"], "image": "postgres:17", "state": "exited", "status": "Exited",
                     "updateInfo": {"hasUpdate": False, "currentVersion": "17", "error": ""}},
                ],
                "projects": [{"id": "p1", "name": "shop", "status": "running",
                              "updateInfo": {"status": "has_update", "hasUpdate": True, "imageCount": 3,
                                             "imagesWithUpdates": 2,
                                             "serviceUpdates": {"app": {"updateInfo": {"hasUpdate": True}},
                                                                "db": {"updateInfo": {"hasUpdate": False}}}}}],
            },
            "h2": {
                "name": "As-VPS",
                "containers": [
                    {"id": "v1", "names": ["/web"], "image": "caddy", "state": "running", "status": "Up",
                     "autoUpdateEnabled": True,
                     "updateInfo": {"hasUpdate": False, "error": "distribution inspect failed: unauthorized"}},
                    # transient artefact while Docker recreates a container: must be ignored
                    {"id": "ag", "names": ["/arcane-edge-agent"], "image": "ghcr.io/getarcaneapp/agent:latest", "state": "running",
                     "status": "Up", "redeployDisabled": True,
                     "updateInfo": {"hasUpdate": True, "updateType": "digest", "currentDigest": "sha256:aaaaaaaaaaaaffff",
                                    "latestDigest": "sha256:bbbbbbbbbbbbffff", "error": ""}},
                    {"id": "v9", "names": ["/8e390ad593fd_web"], "image": "caddy", "state": "exited", "status": "Exited"},
                ],
                "projects": [],
            },
        },
    }


@pytest.fixture
async def server(state, socket_enabled):
    srv = TestServer(make_app(state))
    await srv.start_server()
    yield srv
    await srv.close()
