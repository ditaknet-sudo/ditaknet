"""Topology and maintenance web pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ditaknet import database as db
from ditaknet.i18n import translate
from ditaknet.security import require_web_permissions, verify_web_csrf
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])


def _ctx(request: Request, **extra):
    lang = request.session.get("lang", "en")
    base = {"lang": lang, "t": lambda k, **kw: translate(k, lang, **kw)}
    base.update(extra)
    return base


@router.get("/topology", response_class=HTMLResponse)
async def topology_page(request: Request, user: str = Depends(require_web_permissions("devices.view"))):
    topo = await db.get_topology()
    return render_template(request, "topology/index.html", _ctx(request, topology=topo))


@router.get("/maintenance", response_class=HTMLResponse)
async def maintenance_list(request: Request, user: str = Depends(require_web_permissions("devices.view"))):
    tasks = await db.list_maintenance_tasks()
    return render_template(request, "maintenance/list.html", _ctx(request, tasks=tasks))


@router.get("/maintenance/{task_id}", response_class=HTMLResponse)
async def maintenance_detail(task_id: int, request: Request, user: str = Depends(require_web_permissions("devices.view"))):
    task = await db.get_maintenance_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return render_template(request, "maintenance/detail.html", _ctx(request, task=task))


@router.post("/maintenance/{task_id}/resolve")
async def maintenance_resolve(
    task_id: int,
    request: Request,
    user: str = Depends(require_web_permissions("devices.edit")),
):
    await db.resolve_maintenance_task(task_id)
    return RedirectResponse(url=f"/maintenance/{task_id}", status_code=303)
