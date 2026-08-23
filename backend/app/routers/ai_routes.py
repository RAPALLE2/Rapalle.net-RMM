"""
routers/ai_routes.py
--------------------
AI-Chat: Benutzer legen API-Verbindungen an (API-URL, API-Key, Modell) und
chatten dann über einen Backend-Proxy mit dem Modell.

Sichtbarkeit pro Verbindung (wählt der Ersteller):
  'private' -> nur der Ersteller
  'all'     -> alle Benutzer
  'shared'  -> nur bestimmte Benutzer/Gruppen (ai_connection_shares)

WICHTIG: Der API-Key verlässt das Backend nie - auch der Eigentümer bekommt
ihn nach dem Speichern nicht mehr zurück (nur "gesetzt"-Status). Der Chat
läuft als Proxy: das Frontend schickt Nachrichten, das Backend ruft die
API auf (OpenAI-kompatibles /chat/completions-Format; Anthropic-URLs werden
automatisch im Messages-Format angesprochen).
"""

import json
import time
import urllib.request
import urllib.error
import uuid
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, is_super_admin

router = APIRouter(prefix="/api/ai", tags=["ai"])


# ------------------------------------------------------------------
# Datenbank-Helfer (bewusst hier statt in db.py, um db.py schlank zu halten;
# genutzt wird die zentrale Verbindung über db._conn wie in anderen Routern).
# ------------------------------------------------------------------

def _conn():
    return db._conn


def _shares_of(conn_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT subject_type, subject_id FROM ai_connection_shares WHERE connection_id = ?",
        (conn_id,)).fetchall()
    return [dict(r) for r in rows]


def _user_may_use(user: dict, row: dict) -> bool:
    """Darf der Benutzer diese Verbindung SEHEN/NUTZEN?"""
    if is_super_admin(user) or row["owner_user_id"] == user["id"]:
        return True
    vis = row["visibility"]
    if vis == "all":
        return True
    if vis != "shared":
        return False
    group_ids = set(db.get_user_group_ids(user["id"]))
    for s in _shares_of(row["id"]):
        if s["subject_type"] == "user" and s["subject_id"] == user["id"]:
            return True
        if s["subject_type"] == "group" and s["subject_id"] in group_ids:
            return True
    return False


def _public(row: dict, user: dict) -> dict:
    """Verbindung OHNE API-Key ausliefern."""
    out = {
        "id": row["id"], "name": row["name"], "model": row["model"],
        "visibility": row["visibility"],
        "api_url": row["api_url"],
        "owner_user_id": row["owner_user_id"],
        "is_owner": row["owner_user_id"] == user["id"] or is_super_admin(user),
        "created_at": row["created_at"],
        "has_key": bool(row["api_key"]),
    }
    if out["is_owner"]:
        out["shares"] = _shares_of(row["id"])
    return out


# ------------------------------------------------------------------
# CRUD für Verbindungen
# ------------------------------------------------------------------

class ShareEntry(BaseModel):
    subject_type: str   # 'user' | 'group'
    subject_id: str


class ConnectionBody(BaseModel):
    name: str
    api_url: str
    model: str
    api_key: str | None = None            # beim Bearbeiten: None = Key behalten
    visibility: str = "private"           # 'private' | 'all' | 'shared'
    shares: list[ShareEntry] = []


def _validate_body(body: ConnectionBody, need_key: bool):
    if not body.name.strip():
        raise HTTPException(400, "Name fehlt")
    url = (body.api_url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "API-URL muss mit http(s):// beginnen")
    if not body.model.strip():
        raise HTTPException(400, "Modell fehlt")
    if body.visibility not in ("private", "all", "shared"):
        raise HTTPException(400, "Ungültige Sichtbarkeit")
    if need_key and not (body.api_key or "").strip():
        raise HTTPException(400, "API-Key fehlt")


def _save_shares(conn_id: str, shares: list[ShareEntry]):
    c = _conn()
    c.execute("DELETE FROM ai_connection_shares WHERE connection_id = ?", (conn_id,))
    for s in shares:
        if s.subject_type in ("user", "group") and s.subject_id:
            c.execute(
                "INSERT OR IGNORE INTO ai_connection_shares (connection_id, subject_type, subject_id) "
                "VALUES (?, ?, ?)", (conn_id, s.subject_type, s.subject_id))
    c.commit()


@router.get("/connections")
def list_connections(user: dict = Depends(get_current_user)):
    rows = [dict(r) for r in _conn().execute(
        "SELECT * FROM ai_connections ORDER BY created_at").fetchall()]
    return [_public(r, user) for r in rows if _user_may_use(user, r)]


@router.post("/connections")
def create_connection(body: ConnectionBody, user: dict = Depends(get_current_user)):
    _validate_body(body, need_key=True)
    cid = uuid.uuid4().hex
    _conn().execute(
        "INSERT INTO ai_connections (id, name, api_url, api_key, model, visibility, owner_user_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, body.name.strip(), body.api_url.strip(), body.api_key.strip(),
         body.model.strip(), body.visibility, user["id"], int(time.time() * 1000)))
    _conn().commit()
    if body.visibility == "shared":
        _save_shares(cid, body.shares)
    db.add_audit_entry(user["username"], "ai.connection_created", details=body.name.strip())
    row = dict(_conn().execute("SELECT * FROM ai_connections WHERE id = ?", (cid,)).fetchone())
    return _public(row, user)


@router.put("/connections/{conn_id}")
def update_connection(conn_id: str, body: ConnectionBody,
                            user: dict = Depends(get_current_user)):
    row = _conn().execute("SELECT * FROM ai_connections WHERE id = ?", (conn_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Verbindung nicht gefunden")
    row = dict(row)
    if row["owner_user_id"] != user["id"] and not is_super_admin(user):
        raise HTTPException(403, "Nur der Ersteller kann die Verbindung bearbeiten")
    _validate_body(body, need_key=False)
    new_key = (body.api_key or "").strip() or row["api_key"]   # leer = Key behalten
    _conn().execute(
        "UPDATE ai_connections SET name=?, api_url=?, api_key=?, model=?, visibility=? WHERE id=?",
        (body.name.strip(), body.api_url.strip(), new_key, body.model.strip(),
         body.visibility, conn_id))
    _conn().commit()
    _save_shares(conn_id, body.shares if body.visibility == "shared" else [])
    row = dict(_conn().execute("SELECT * FROM ai_connections WHERE id = ?", (conn_id,)).fetchone())
    return _public(row, user)


@router.delete("/connections/{conn_id}")
def delete_connection(conn_id: str, user: dict = Depends(get_current_user)):
    row = _conn().execute("SELECT * FROM ai_connections WHERE id = ?", (conn_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Verbindung nicht gefunden")
    if row["owner_user_id"] != user["id"] and not is_super_admin(user):
        raise HTTPException(403, "Nur der Ersteller kann die Verbindung löschen")
    _conn().execute("DELETE FROM ai_connections WHERE id = ?", (conn_id,))
    _conn().execute("DELETE FROM ai_connection_shares WHERE connection_id = ?", (conn_id,))
    _conn().commit()
    db.add_audit_entry(user["username"], "ai.connection_deleted", details=row["name"])
    return {"ok": True}


# Auswahl-Listen für den Freigabe-Dialog (Name + ID reichen).
@router.get("/share-subjects")
def share_subjects(user: dict = Depends(get_current_user)):
    users = [{"id": u["id"], "label": u.get("display_name") or u["username"]}
             for u in db.list_users()]
    groups = [{"id": g["id"], "label": g["name"]} for g in db.list_groups()]
    return {"users": users, "groups": groups}


# ------------------------------------------------------------------
# Chat-Proxy
# ------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str      # 'user' | 'assistant' | 'system'
    content: str


class ChatBody(BaseModel):
    connection_id: str
    messages: list[ChatMessage]


def _call_ai(api_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    """Blockierender API-Aufruf (läuft im Executor). Nutzt nur die
    Standardbibliothek (urllib), damit keine neue Abhängigkeit nötig ist."""
    url = api_url.rstrip("/")
    is_anthropic = "anthropic" in url.lower()

    if is_anthropic:
        # Anthropic Messages-API: system separat, eigener Auth-Header.
        if not url.endswith("/v1/messages"):
            url += "/v1/messages"
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        payload = {
            "model": model, "max_tokens": 2048,
            "messages": [m for m in messages if m["role"] in ("user", "assistant")],
        }
        if system:
            payload["system"] = system
        headers = {"Content-Type": "application/json", "x-api-key": api_key,
                   "anthropic-version": "2023-06-01"}
    else:
        # OpenAI-kompatibel (OpenAI, Azure-Gateways, Ollama, LM Studio,
        # OpenRouter, vLLM, ...): /chat/completions mit Bearer-Token.
        if not url.endswith("/chat/completions"):
            if url.endswith("/v1"):
                url += "/chat/completions"
            else:
                url += "/v1/chat/completions"
        payload = {"model": model, "messages": messages}
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {api_key}"}

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise HTTPException(502, f"AI-API Fehler {e.code}: {detail}")
    except Exception as e:
        raise HTTPException(502, f"AI-API nicht erreichbar: {e}")

    # Antwort-Text aus beiden Formaten extrahieren.
    if is_anthropic:
        parts = data.get("content") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if text:
            return text
    else:
        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            if msg.get("content"):
                return msg["content"]
    raise HTTPException(502, "AI-API lieferte keine verwertbare Antwort")


@router.post("/chat")
async def chat(body: ChatBody, user: dict = Depends(get_current_user)):
    row = _conn().execute("SELECT * FROM ai_connections WHERE id = ?",
                          (body.connection_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Verbindung nicht gefunden")
    row = dict(row)
    if not _user_may_use(user, row):
        raise HTTPException(403, "Keine Berechtigung für diese Verbindung")
    if len(body.messages) > 100:
        raise HTTPException(400, "Zu viele Nachrichten")
    messages = [{"role": m.role, "content": m.content} for m in body.messages
                if m.role in ("user", "assistant", "system")]
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(
        None, _call_ai, row["api_url"], row["api_key"], row["model"], messages)
    return {"reply": text, "model": row["model"]}
