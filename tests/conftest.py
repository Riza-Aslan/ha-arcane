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
    return app


@pytest.fixture
def state():
    return {
        "calls": [],
        "hosts": {
            "0": {
                "name": "Local Docker",
                "containers": [
                    {"id": "c1", "names": ["/web"], "image": "nginx:latest", "state": "running", "status": "Up 2h",
                     "updateInfo": {"hasUpdate": True, "currentVersion": "1.27", "latestVersion": "1.28", "updateType": "tag", "error": ""}},
                    {"id": "c2", "names": ["/db"], "image": "postgres:17", "state": "exited", "status": "Exited",
                     "updateInfo": {"hasUpdate": False, "currentVersion": "17", "error": ""}},
                ],
                "projects": [{"id": "p1", "name": "shop", "status": "running"}],
            },
            "h2": {
                "name": "As-VPS",
                "containers": [
                    {"id": "v1", "names": ["/web"], "image": "caddy", "state": "running", "status": "Up"},
                    # transient artefact while Docker recreates a container: must be ignored
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
