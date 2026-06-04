"""Generic CRUD router shared by every resource (thin controller).

One implementation serves all resources; the registry drives validation. Routes
delegate to the service and translate nothing — errors bubble to the global
handlers as structured responses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Response

from app.api.dependencies import get_resource_service
from app.domain.registry import get_resource
from app.services.resource_service import ResourceService

router = APIRouter(prefix="/api", tags=["resources"])

Document = dict[str, Any]


@router.get("/{resource}")
def list_all(resource: str, service: ResourceService = Depends(get_resource_service)) -> list[Document]:
    return service.list(get_resource(resource))


@router.get("/{resource}/{item_id}")
def get_one(resource: str, item_id: str, service: ResourceService = Depends(get_resource_service)) -> Document:
    return service.get(get_resource(resource), item_id)


@router.post("/{resource}", status_code=201)
def create(resource: str, payload: Document = Body(...), service: ResourceService = Depends(get_resource_service)) -> Document:
    return service.create(get_resource(resource), payload)


@router.put("/{resource}/{item_id}")
def replace(resource: str, item_id: str, payload: Document = Body(...), service: ResourceService = Depends(get_resource_service)) -> Document:
    return service.replace(get_resource(resource), item_id, payload)


@router.patch("/{resource}/{item_id}")
def patch(resource: str, item_id: str, changes: Document = Body(...), service: ResourceService = Depends(get_resource_service)) -> Document:
    return service.patch(get_resource(resource), item_id, changes)


@router.delete("/{resource}/{item_id}", status_code=204, response_class=Response)
def remove(resource: str, item_id: str, service: ResourceService = Depends(get_resource_service)) -> Response:
    service.delete(get_resource(resource), item_id)
    return Response(status_code=204)
