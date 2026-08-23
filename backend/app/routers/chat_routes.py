"""
routers/chat_routes.py
----------------------
Chat-System (WhatsApp-artig) zwischen Dashboard-Benutzern:
  - Direktnachrichten (dm): genau 2 Teilnehmer, wird pro Paar dedupliziert
  - Gruppen (group): Name, beliebig viele Mitglieder, Gruppen-Admins
    (Ersteller ist automatisch Admin; Admins können umbenennen, Mitglieder
    hinzufügen/entfernen, Admin-Status vergeben und die Gruppe löschen)
  - Ungelesen-Zähler über chat_members.last_read_at (für die Login-Anzeige
    "X neue Nachrichten" und die Badges in der App)

Berechtigung: ALLE Chat-Routen erfordern das globale Recht 'use_chat'
(Super-Admins implizit). Neue Nachrichten werden live per Socket-Event
"chat:message" an alle Dashboards verteilt; die Clients filtern selbst,
ob sie Mitglied der Unterhaltung sind (Inhalt kommt sowieso nur per REST,
das Event trägt nur Metadaten für Badge/Toast).
"""

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user, require_perm, is_super_admin

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_TEXT = 4000


def _conn():
    return db._conn


def _now() -> int:
    return int(time.time() * 1000)


def _require_chat(user: dict) -> None:
    require_perm(user, "use_chat")


def _member_row(conv_id: str, user_id: str):
    return _conn().execute(
        "SELECT * FROM chat_members WHERE conversation_id = ? AND user_id = ?",
        (conv_id, user_id)).fetchone()


def _require_member(conv_id: str, user: dict) -> dict:
    conv = _conn().execute(
        "SELECT * FROM chat_conversations WHERE id = ?", (conv_id,)).fetchone()
    if not conv:
        raise HTTPException(404, "Unterhaltung nicht gefunden")
    m = _member_row(conv_id, user["id"])
    if not m and not is_super_admin(user):
        raise HTTPException(403, "Kein Mitglied dieser Unterhaltung")
    return dict(conv)


def _require_group_admin(conv: dict, user: dict) -> None:
    if is_super_admin(user):
        return
    m = _member_row(conv["id"], user["id"])
    if not m or not m["is_admin"]:
        raise HTTPException(403, "Nur Gruppen-Admins dürfen das")


def _usernames() -> dict:
    return {u["id"]: u["username"] for u in db.list_users()}


def _members_of(conv_id: str) -> list[dict]:
    names = _usernames()
    rows = _conn().execute(
        "SELECT user_id, is_admin FROM chat_members WHERE conversation_id = ?"
        " ORDER BY joined_at", (conv_id,)).fetchall()
    return [{"user_id": r["user_id"], "username": names.get(r["user_id"], "?"),
             "is_admin": bool(r["is_admin"])} for r in rows]


def _conv_summary(conv: dict, me: dict) -> dict:
    """Listen-Eintrag: Anzeigename, letzte Nachricht, Ungelesen-Zähler."""
    members = _members_of(conv["id"])
    last = _conn().execute(
        "SELECT m.*, u.username AS sender_name FROM chat_messages m"
        " LEFT JOIN users u ON u.id = m.sender_id"
        " WHERE m.conversation_id = ? ORDER BY m.created_at DESC LIMIT 1",
        (conv["id"],)).fetchone()
    my = _member_row(conv["id"], me["id"])
    last_read = my["last_read_at"] if my else 0
    unread = _conn().execute(
        "SELECT COUNT(*) AS c FROM chat_messages"
        " WHERE conversation_id = ? AND created_at > ? AND sender_id != ?",
        (conv["id"], last_read, me["id"])).fetchone()["c"]
    if conv["type"] == "dm":
        other = next((m for m in members if m["user_id"] != me["id"]), None)
        display = other["username"] if other else "(gelöschter Benutzer)"
    else:
        display = conv["name"] or "Gruppe"
    return {
        "id": conv["id"], "type": conv["type"], "name": display,
        "members": members,
        "is_admin": bool(my and my["is_admin"]) or is_super_admin(me),
        "unread": unread,
        "last_message": ({"text": last["text"], "sender": last["sender_name"],
                          "created_at": last["created_at"]} if last else None),
    }


async def _emit_chat_event(event: str, payload: dict) -> None:
    try:
        from app.sockets import sio
        await sio.emit(event, payload, namespace="/dashboard")
    except Exception:
        pass


# ------------------------------------------------------------------
# Benutzer-/Unterhaltungs-Listen
# ------------------------------------------------------------------

@router.get("/users")
def chat_users(user: dict = Depends(get_current_user)):
    """Alle Benutzer als Chat-Partner-Auswahl (ohne sich selbst)."""
    _require_chat(user)
    return [{"id": u["id"], "username": u["username"]}
            for u in db.list_users() if u["id"] != user["id"]]


@router.get("/conversations")
def list_conversations(user: dict = Depends(get_current_user)):
    _require_chat(user)
    rows = _conn().execute(
        "SELECT c.* FROM chat_conversations c"
        " JOIN chat_members m ON m.conversation_id = c.id"
        " WHERE m.user_id = ?", (user["id"],)).fetchall()
    out = [_conv_summary(dict(r), user) for r in rows]
    # Neueste Aktivität zuerst
    out.sort(key=lambda c: (c["last_message"] or {}).get("created_at", 0), reverse=True)
    return out


@router.get("/unread")
def unread_summary(user: dict = Depends(get_current_user)):
    """Für die Anzeige beim Login: wie viele ungelesene Nachrichten, von wem."""
    _require_chat(user)
    # list_conversations() laeuft jetzt synchron im Arbeits-Thread
    # (siehe tools/unblock_routes.py) - hier darf kein 'await' mehr stehen.
    convs = list_conversations(user)
    items = [{"conversation_id": c["id"], "name": c["name"], "unread": c["unread"],
              "last_message": c["last_message"]}
             for c in convs if c["unread"] > 0]
    return {"total": sum(i["unread"] for i in items), "conversations": items}


# ------------------------------------------------------------------
# Unterhaltungen anlegen
# ------------------------------------------------------------------

class CreateConversationBody(BaseModel):
    type: str = "dm"                      # 'dm' | 'group'
    user_id: str | None = None            # dm: der Gesprächspartner
    name: str | None = None               # group: Gruppenname
    member_ids: list[str] | None = None   # group: Start-Mitglieder


@router.post("/conversations")
async def create_conversation(body: CreateConversationBody,
                              user: dict = Depends(get_current_user)):
    _require_chat(user)
    now = _now()
    if body.type == "dm":
        if not body.user_id or not db.get_user_by_id(body.user_id):
            raise HTTPException(400, "Gesprächspartner nicht gefunden")
        if body.user_id == user["id"]:
            raise HTTPException(400, "Selbstgespräche sind nicht vorgesehen 🙂")
        # Bestehendes DM-Paar wiederverwenden
        row = _conn().execute(
            "SELECT c.id FROM chat_conversations c"
            " JOIN chat_members a ON a.conversation_id = c.id AND a.user_id = ?"
            " JOIN chat_members b ON b.conversation_id = c.id AND b.user_id = ?"
            " WHERE c.type = 'dm'", (user["id"], body.user_id)).fetchone()
        if row:
            conv = _conn().execute("SELECT * FROM chat_conversations WHERE id = ?",
                                   (row["id"],)).fetchone()
            return _conv_summary(dict(conv), user)
        cid = str(uuid.uuid4())
        _conn().execute(
            "INSERT INTO chat_conversations (id, type, name, created_by, created_at)"
            " VALUES (?, 'dm', NULL, ?, ?)", (cid, user["id"], now))
        for uid in (user["id"], body.user_id):
            _conn().execute(
                "INSERT INTO chat_members (conversation_id, user_id, is_admin, joined_at)"
                " VALUES (?, ?, 0, ?)", (cid, uid, now))
    elif body.type == "group":
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(400, "Gruppenname erforderlich")
        cid = str(uuid.uuid4())
        _conn().execute(
            "INSERT INTO chat_conversations (id, type, name, created_by, created_at)"
            " VALUES (?, 'group', ?, ?, ?)", (cid, name, user["id"], now))
        # Ersteller ist automatisch Gruppen-Admin
        _conn().execute(
            "INSERT INTO chat_members (conversation_id, user_id, is_admin, joined_at)"
            " VALUES (?, ?, 1, ?)", (cid, user["id"], now))
        for uid in set(body.member_ids or []):
            if uid != user["id"] and db.get_user_by_id(uid):
                _conn().execute(
                    "INSERT OR IGNORE INTO chat_members"
                    " (conversation_id, user_id, is_admin, joined_at)"
                    " VALUES (?, ?, 0, ?)", (cid, uid, now))
    else:
        raise HTTPException(400, "Ungültiger Typ (dm|group)")
    _conn().commit()
    conv = _conn().execute("SELECT * FROM chat_conversations WHERE id = ?", (cid,)).fetchone()
    await _emit_chat_event("chat:changed", {"conversation_id": cid})
    return _conv_summary(dict(conv), user)


# ------------------------------------------------------------------
# Nachrichten
# ------------------------------------------------------------------

@router.get("/conversations/{conv_id}/messages")
def get_messages(conv_id: str, before: int | None = None, limit: int = 50,
                       user: dict = Depends(get_current_user)):
    _require_chat(user)
    _require_member(conv_id, user)
    limit = max(1, min(int(limit or 50), 200))
    params: list = [conv_id]
    where = "conversation_id = ?"
    if before:
        where += " AND created_at < ?"
        params.append(int(before))
    rows = _conn().execute(
        f"SELECT m.*, u.username AS sender_name FROM chat_messages m"
        f" LEFT JOIN users u ON u.id = m.sender_id"
        f" WHERE {where} ORDER BY m.created_at DESC LIMIT ?",
        (*params, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


class MessageBody(BaseModel):
    text: str


@router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: str, body: MessageBody,
                       user: dict = Depends(get_current_user)):
    _require_chat(user)
    conv = _require_member(conv_id, user)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "Leere Nachricht")
    if len(text) > MAX_TEXT:
        raise HTTPException(400, f"Nachricht zu lang (max. {MAX_TEXT} Zeichen)")
    mid = str(uuid.uuid4())
    now = _now()
    _conn().execute(
        "INSERT INTO chat_messages (id, conversation_id, sender_id, text, created_at)"
        " VALUES (?, ?, ?, ?, ?)", (mid, conv_id, user["id"], text, now))
    # Eigene Nachricht gilt sofort als gelesen
    _conn().execute(
        "UPDATE chat_members SET last_read_at = ? WHERE conversation_id = ? AND user_id = ?",
        (now, conv_id, user["id"]))
    _conn().commit()
    member_ids = [m["user_id"] for m in _members_of(conv_id)]
    display = conv["name"] if conv["type"] == "group" else user["username"]
    await _emit_chat_event("chat:message", {
        "conversation_id": conv_id, "conversation_type": conv["type"],
        "conversation_name": display,
        "sender_id": user["id"], "sender_name": user["username"],
        "member_ids": member_ids,
        "preview": text[:140], "created_at": now,
    })
    return {"id": mid, "created_at": now}


@router.post("/conversations/{conv_id}/read")
def mark_read(conv_id: str, user: dict = Depends(get_current_user)):
    _require_chat(user)
    _require_member(conv_id, user)
    _conn().execute(
        "UPDATE chat_members SET last_read_at = ? WHERE conversation_id = ? AND user_id = ?",
        (_now(), conv_id, user["id"]))
    _conn().commit()
    return {"ok": True}


# ------------------------------------------------------------------
# Gruppen-Verwaltung (nur Gruppen-Admins)
# ------------------------------------------------------------------

class RenameBody(BaseModel):
    name: str


@router.patch("/conversations/{conv_id}")
async def rename_group(conv_id: str, body: RenameBody,
                       user: dict = Depends(get_current_user)):
    _require_chat(user)
    conv = _require_member(conv_id, user)
    if conv["type"] != "group":
        raise HTTPException(400, "Nur Gruppen können umbenannt werden")
    _require_group_admin(conv, user)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "Name erforderlich")
    _conn().execute("UPDATE chat_conversations SET name = ? WHERE id = ?", (name, conv_id))
    _conn().commit()
    await _emit_chat_event("chat:changed", {"conversation_id": conv_id})
    return {"ok": True}


class MemberBody(BaseModel):
    user_id: str


@router.post("/conversations/{conv_id}/members")
async def add_member(conv_id: str, body: MemberBody,
                     user: dict = Depends(get_current_user)):
    _require_chat(user)
    conv = _require_member(conv_id, user)
    if conv["type"] != "group":
        raise HTTPException(400, "Mitglieder gibt es nur in Gruppen")
    _require_group_admin(conv, user)
    if not db.get_user_by_id(body.user_id):
        raise HTTPException(404, "Benutzer nicht gefunden")
    _conn().execute(
        "INSERT OR IGNORE INTO chat_members (conversation_id, user_id, is_admin, joined_at)"
        " VALUES (?, ?, 0, ?)", (conv_id, body.user_id, _now()))
    _conn().commit()
    await _emit_chat_event("chat:changed", {"conversation_id": conv_id})
    return {"ok": True}


@router.delete("/conversations/{conv_id}/members/{member_id}")
async def remove_member(conv_id: str, member_id: str,
                        user: dict = Depends(get_current_user)):
    _require_chat(user)
    conv = _require_member(conv_id, user)
    if conv["type"] != "group":
        raise HTTPException(400, "Mitglieder gibt es nur in Gruppen")
    if member_id != user["id"]:          # sich selbst entfernen = Gruppe verlassen
        _require_group_admin(conv, user)
    _conn().execute(
        "DELETE FROM chat_members WHERE conversation_id = ? AND user_id = ?",
        (conv_id, member_id))
    # Verwaiste Gruppe ohne Admin: dienstältestes Mitglied wird Admin.
    admins = _conn().execute(
        "SELECT COUNT(*) AS c FROM chat_members WHERE conversation_id = ? AND is_admin = 1",
        (conv_id,)).fetchone()["c"]
    if admins == 0:
        oldest = _conn().execute(
            "SELECT user_id FROM chat_members WHERE conversation_id = ?"
            " ORDER BY joined_at LIMIT 1", (conv_id,)).fetchone()
        if oldest:
            _conn().execute(
                "UPDATE chat_members SET is_admin = 1 WHERE conversation_id = ? AND user_id = ?",
                (conv_id, oldest["user_id"]))
        else:
            _conn().execute("DELETE FROM chat_conversations WHERE id = ?", (conv_id,))
    _conn().commit()
    await _emit_chat_event("chat:changed", {"conversation_id": conv_id})
    return {"ok": True}


class AdminBody(BaseModel):
    user_id: str
    is_admin: bool


@router.post("/conversations/{conv_id}/admins")
async def set_group_admin(conv_id: str, body: AdminBody,
                          user: dict = Depends(get_current_user)):
    _require_chat(user)
    conv = _require_member(conv_id, user)
    if conv["type"] != "group":
        raise HTTPException(400, "Admins gibt es nur in Gruppen")
    _require_group_admin(conv, user)
    _conn().execute(
        "UPDATE chat_members SET is_admin = ? WHERE conversation_id = ? AND user_id = ?",
        (int(body.is_admin), conv_id, body.user_id))
    _conn().commit()
    await _emit_chat_event("chat:changed", {"conversation_id": conv_id})
    return {"ok": True}


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user: dict = Depends(get_current_user)):
    """Gruppe löschen (Gruppen-Admin) bzw. DM für BEIDE entfernen (Teilnehmer)."""
    _require_chat(user)
    conv = _require_member(conv_id, user)
    if conv["type"] == "group":
        _require_group_admin(conv, user)
    _conn().execute("DELETE FROM chat_conversations WHERE id = ?", (conv_id,))
    _conn().commit()
    await _emit_chat_event("chat:changed", {"conversation_id": conv_id})
    return {"ok": True}
