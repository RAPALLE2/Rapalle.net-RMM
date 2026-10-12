"""Nur aktiv, wenn die Teilfunktion 'crash_demo' eingeschaltet ist."""

from fastapi import APIRouter, Depends

from app.auth import get_current_user

router = APIRouter()


@router.get("/api/example/crash")
def crash(user: dict = Depends(get_current_user)):
    raise RuntimeError("Absichtlicher Absturz des Beispiel-Moduls (Crash-Demo)")
