"""
org.py
------
Organisations-Hierarchie: Wer ist wem unterstellt?

Ein Knoten ist entweder ein BENUTZER oder eine GRUPPE und hat höchstens
einen Vorgesetzten (ebenfalls Benutzer oder Gruppe). Daraus entsteht ein
Baum, den die Organigramm-App darstellt.

Wozu das Ganze: Vorgesetzte dürfen die Kalender ihrer Untergebenen einsehen
und dort Termine eintragen (siehe routers/calendar_routes.py).

Wichtig: "unterstellt" wird transitiv ausgewertet - der Chef vom Chef sieht
also auch die Ebene darunter. Zusätzlich zählt die Gruppenmitgliedschaft:
Ist eine GRUPPE jemandem unterstellt, gilt das auch für ihre Mitglieder.
"""

import time

from app import db

NODE_TYPES = ("user", "group")


def _now() -> int:
    return int(time.time() * 1000)


def key(ntype: str, nid: str) -> str:
    return f"{ntype}:{nid}"


# ------------------------------------------------------------------
# Lesen
# ------------------------------------------------------------------

def all_links() -> list[dict]:
    return [dict(r) for r in db._conn.execute("SELECT * FROM org_hierarchy")]


def parent_of(ntype: str, nid: str) -> tuple[str, str] | None:
    row = db._conn.execute(
        "SELECT parent_type, parent_id FROM org_hierarchy"
        " WHERE child_type = ? AND child_id = ?", (ntype, nid)).fetchone()
    if not row or not row["parent_id"]:
        return None
    return (row["parent_type"], row["parent_id"])


def children_of(ntype: str | None, nid: str | None) -> list[tuple[str, str]]:
    if ntype is None:
        rows = db._conn.execute(
            "SELECT child_type, child_id FROM org_hierarchy WHERE parent_id IS NULL").fetchall()
    else:
        rows = db._conn.execute(
            "SELECT child_type, child_id FROM org_hierarchy"
            " WHERE parent_type = ? AND parent_id = ?", (ntype, nid)).fetchall()
    return [(r["child_type"], r["child_id"]) for r in rows]


def set_parent(child_type: str, child_id: str,
               parent_type: str | None, parent_id: str | None) -> None:
    """Setzt den Vorgesetzten. parent=None -> oberste Ebene.
    Verhindert Zyklen (ein Knoten darf nicht sein eigener Vorgesetzter werden)."""
    if child_type not in NODE_TYPES:
        raise ValueError("child_type muss 'user' oder 'group' sein")
    if parent_type and parent_type not in NODE_TYPES:
        raise ValueError("parent_type muss 'user' oder 'group' sein")
    if parent_id and (parent_type, parent_id) == (child_type, child_id):
        raise ValueError("Ein Knoten kann sich nicht selbst unterstellt sein")
    if parent_id:
        # Wäre der neue Vorgesetzte bereits ein Untergebener? -> Zyklus
        cur = (parent_type, parent_id)
        seen = set()
        while cur:
            if cur == (child_type, child_id):
                raise ValueError("Das würde einen Kreis erzeugen "
                                 "(der neue Vorgesetzte ist bereits untergeordnet)")
            if key(*cur) in seen:
                break
            seen.add(key(*cur))
            cur = parent_of(*cur)
    db._conn.execute(
        "INSERT INTO org_hierarchy (child_type, child_id, parent_type, parent_id, updated_at)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(child_type, child_id) DO UPDATE SET"
        " parent_type = excluded.parent_type, parent_id = excluded.parent_id,"
        " updated_at = excluded.updated_at",
        (child_type, child_id, parent_type, parent_id, _now()))
    db._conn.commit()


def remove_node(ntype: str, nid: str) -> None:
    """Beim Löschen eines Benutzers/einer Gruppe aufräumen: eigene Zeile weg,
    Untergebene rutschen eine Ebene nach oben."""
    parent = parent_of(ntype, nid)
    db._conn.execute("DELETE FROM org_hierarchy WHERE child_type = ? AND child_id = ?",
                     (ntype, nid))
    db._conn.execute(
        "UPDATE org_hierarchy SET parent_type = ?, parent_id = ?, updated_at = ?"
        " WHERE parent_type = ? AND parent_id = ?",
        (parent[0] if parent else None, parent[1] if parent else None, _now(), ntype, nid))
    db._conn.commit()


# ------------------------------------------------------------------
# Untergebene ermitteln (transitiv)
# ------------------------------------------------------------------

def descendants(ntype: str, nid: str) -> set[str]:
    """Alle (transitiv) untergeordneten Knoten als {"user:id", "group:id", …}."""
    out: set[str] = set()
    stack = [(ntype, nid)]
    while stack:
        cur = stack.pop()
        for child in children_of(*cur):
            k = key(*child)
            if k in out:
                continue
            out.add(k)
            stack.append(child)
    return out


def subordinate_user_ids(user: dict) -> set[str]:
    """
    Alle Benutzer, die diesem Benutzer unterstellt sind - direkt, über
    Zwischenebenen oder weil sie in einer unterstellten GRUPPE sind.
    Zusätzlich zählen die Gruppen des Benutzers selbst als Ausgangspunkt:
    Ist eine Gruppe Vorgesetzte, gilt das für alle ihre Mitglieder.
    """
    roots = [("user", user["id"])]
    for gid in db.get_user_group_ids(user["id"]):
        roots.append(("group", gid))

    nodes: set[str] = set()
    for r in roots:
        nodes |= descendants(*r)

    users: set[str] = set()
    for k in nodes:
        t, _, i = k.partition(":")
        if t == "user":
            users.add(i)
        elif t == "group":
            # Alle Mitglieder einer unterstellten Gruppe gelten als unterstellt
            rows = db._conn.execute(
                "SELECT user_id FROM user_groups WHERE group_id = ?", (i,)).fetchall()
            users.update(r["user_id"] for r in rows)
    users.discard(user["id"])
    return users


def is_supervisor_of(user: dict, target_user_id: str) -> bool:
    return target_user_id in subordinate_user_ids(user)


# ------------------------------------------------------------------
# Baum fürs Frontend
# ------------------------------------------------------------------

def build_tree() -> dict:
    """
    Liefert Knoten + Kanten für die Organigramm-App:
      {"nodes": [{type,id,name,workspace,role,is_ad_group}], "links": {childKey: parentKey}}
    Knoten ohne Eintrag in org_hierarchy gelten als oberste Ebene.
    """
    users = db.list_users()
    groups = db.list_groups()
    links = {}
    for r in all_links():
        if r["parent_id"]:
            links[key(r["child_type"], r["child_id"])] = key(r["parent_type"], r["parent_id"])

    nodes = []
    for u in users:
        nodes.append({
            "type": "user", "id": u["id"],
            "name": u.get("display_name") or u["username"],
            "username": u["username"],
            "workspace": u.get("workspace") or "",
            "role": u.get("role"),
        })
    for g in groups:
        nodes.append({
            "type": "group", "id": g["id"], "name": g["name"],
            "is_ad_group": bool(g.get("is_ad_group")),
            "unmanaged": bool(g.get("unmanaged")),
            "workspace": "",
        })
    return {"nodes": nodes, "links": links}
