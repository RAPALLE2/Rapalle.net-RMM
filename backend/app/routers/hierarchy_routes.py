"""
routers/hierarchy_routes.py
-----------------------------
Verwaltung der Tenant -> Location -> Folder Struktur, die die Sidebar
im Dashboard darstellt (ähnlich Proxmox "Datacenter -> Node").

Endpunkte:
  GET  /api/hierarchy            -> komplette Struktur auf einmal (für die Sidebar)
  POST /api/tenants               -> neuen Tenant (Kunde/Firma) anlegen
  POST /api/locations              -> neue Location innerhalb eines Tenants anlegen
  POST /api/folders                -> neuen (ggf. verschachtelten) Ordner anlegen
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_admin

router = APIRouter(tags=["hierarchy"])


class CreateTenantBody(BaseModel):
    name: str
    color: str = "#2dd4bf"


class CreateLocationBody(BaseModel):
    tenant_id: str
    name: str


class CreateFolderBody(BaseModel):
    location_id: str
    name: str
    parent_folder_id: str | None = None


@router.get("/api/hierarchy")
async def get_hierarchy(user: dict = Depends(get_current_user)):
    """
    Liefert Tenants, Locations und Folders in einem einzigen Aufruf zurück.
    Das Frontend baut daraus lokal den Baum für die Sidebar zusammen -
    das ist einfacher, als für jede Ebene einzeln nachzufragen.
    """
    return {
        "tenants": db.list_tenants(),
        "locations": db.list_locations(),
        "folders": db.list_folders(),
    }


@router.post("/api/tenants")
async def create_tenant(body: CreateTenantBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    tenant = db.create_tenant(body.name, body.color)
    db.add_audit_entry(user["username"], "tenant.created", target=tenant["id"], details=body.name)
    return tenant


@router.post("/api/locations")
async def create_location(body: CreateLocationBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    location = db.create_location(body.tenant_id, body.name)
    db.add_audit_entry(user["username"], "location.created", target=location["id"], details=body.name)
    return location


@router.post("/api/folders")
async def create_folder(body: CreateFolderBody, user: dict = Depends(get_current_user)):
    require_admin(user)
    folder = db.create_folder(body.location_id, body.name, body.parent_folder_id)
    db.add_audit_entry(user["username"], "folder.created", target=folder["id"], details=body.name)
    return folder


@router.delete("/api/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str, user: dict = Depends(get_current_user)):
    """
    Löscht einen Tenant mitsamt Locations/Ordnern. Alle betroffenen Clients
    werden automatisch nach Uncategorized/Default verschoben (nichts geht verloren).
    """
    require_admin(user)
    try:
        result = db.delete_tenant(tenant_id)
    except KeyError:
        raise HTTPException(404, "Tenant nicht gefunden")
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.add_audit_entry(user["username"], "tenant.deleted", target=tenant_id,
                       details=f"moved_clients:{result['moved_clients']}")
    return {"ok": True, **result}


@router.delete("/api/locations/{location_id}")
async def delete_location(location_id: str, user: dict = Depends(get_current_user)):
    """
    Löscht eine Location mitsamt Ordnern. Alle betroffenen Clients werden
    automatisch nach Uncategorized/Default verschoben (nichts geht verloren).
    """
    require_admin(user)
    try:
        result = db.delete_location(location_id)
    except KeyError:
        raise HTTPException(404, "Location nicht gefunden")
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.add_audit_entry(user["username"], "location.deleted", target=location_id,
                       details=f"moved_clients:{result['moved_clients']}")
    return {"ok": True, **result}
