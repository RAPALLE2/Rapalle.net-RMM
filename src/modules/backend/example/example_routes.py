"""Beispiel-Router: normale FastAPI-Endpunkte. Nur Login noetig."""

from fastapi import APIRouter, Depends

from app import modules
from app.auth import get_current_user

router = APIRouter()


@router.get("/api/example/hello")
def hello(user: dict = Depends(get_current_user)):
    text = str(modules.setting("example", "greeting", "Hallo"))
    if modules.setting("example", "shout", False):
        text = text.upper()
    return {"message": text, "user": user["username"]}
