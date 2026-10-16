"""Test fixtures: a fake Arcane server speaking the real API shapes."""

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
        async def h(_request):
            return fn()

        r.add_get(path, h)

    get("/api/health", lambda: web.json_response({"status": "UP"}))
    get("/api/app-version", lambda: _env({"currentVersion": "2.10.0", "displayVersion": "v2.10.0"}))
    get("/api/environments/0/containers/counts", lambda: _env({"runningContainers": 1, "stoppedContainers": 1, "totalContainers": 2}))
    get("/api/environments/0/projects/counts", lambda: _env({"runningProjects": 1, "stoppedProjects": 0, "totalProjects": 1}))
    get("/api/environments/0/image-updates/summary", lambda: _env({"totalImages": 2, "imagesWithUpdates": 1, "digestUpdates": 1, "errorsCount": 0}))
    get("/api/environments/0/containers", lambda: _page(state["containers"]))
    get("/api/environments/0/projects", lambda: _page(state["projects"]))

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
        r.add_post(f"/api/environments/0/containers/{{cid}}/{p}", action)
    r.add_post("/api/environments/0/projects/{pid}/up", up)
    r.add_post("/api/environments/0/projects/{pid}/down", action)
    r.add_post("/api/environments/0/projects/{pid}/restart", action)
    r.add_post("/api/environments/0/image-updates/check-all", action)
    return app


@pytest.fixture
def state():
    return {
        "calls": [],
        "containers": [
            {"id": "c1", "names": ["/web"], "image": "nginx:latest", "state": "running", "status": "Up 2h",
             "updateInfo": {"hasUpdate": True, "currentVersion": "1.27", "latestVersion": "1.28", "updateType": "tag", "error": ""}},
            {"id": "c2", "names": ["/db"], "image": "postgres:17", "state": "exited", "status": "Exited",
             "updateInfo": {"hasUpdate": False, "currentVersion": "17", "error": ""}},
        ],
        "projects": [{"id": "p1", "name": "shop", "status": "running"}],
    }


@pytest.fixture
async def server(state, socket_enabled):
    srv = TestServer(make_app(state))
    await srv.start_server()
    yield srv
    await srv.close()
