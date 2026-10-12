"""
routers/games_routes.py
-----------------------
Scoreboard für den Gaming Hub: Pro Spiel wird der BESTE Score jedes Benutzers
gespeichert (Upsert nur bei Verbesserung). Kein eigenes Recht nötig - wer
spielen darf (play_games gated die App im Frontend), darf auch Scores melden.
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import db
from app.auth import get_current_user

router = APIRouter(prefix="/api/games", tags=["games"])

# Nur bekannte Spiele annehmen (verhindert Müll-Einträge).
KNOWN_GAMES = {
    "towerdefense": "🛡️ Tower Defense (Wellen)",
    "snake": "🐍 Snake (Punkte)",
    "tetris": "🧱 Tetris (Punkte)",
    "wordle": "🟩 Wordle (Punkte)",
    "minesweeper": "💣 Minesweeper (Punkte)",
    "sudoku": "🔢 Sudoku (Punkte)",
}


def _ensure_table():
    db._conn.execute("""
        CREATE TABLE IF NOT EXISTS game_scores (
            game TEXT NOT NULL,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (game, user_id)
        )
    """)
    db._conn.commit()


class ScoreBody(BaseModel):
    game: str
    score: int


@router.get("/scores")
def get_scores(user: dict = Depends(get_current_user)):
    """Top-10 pro Spiel (bester Score je Benutzer, absteigend)."""
    _ensure_table()
    out = {}
    for game, label in KNOWN_GAMES.items():
        rows = db._conn.execute(
            "SELECT username, score, updated_at FROM game_scores "
            "WHERE game = ? ORDER BY score DESC, updated_at ASC LIMIT 10",
            (game,)).fetchall()
        out[game] = {"label": label, "entries": [dict(r) for r in rows]}
    return out


@router.post("/scores")
def submit_score(body: ScoreBody, user: dict = Depends(get_current_user)):
    """Score melden - gespeichert wird nur, wenn er den bisherigen Bestwert
    des Benutzers übertrifft."""
    _ensure_table()
    if body.game not in KNOWN_GAMES:
        raise HTTPException(400, "Unbekanntes Spiel")
    score = max(0, min(int(body.score), 1_000_000_000))
    row = db._conn.execute(
        "SELECT score FROM game_scores WHERE game = ? AND user_id = ?",
        (body.game, user["id"])).fetchone()
    improved = row is None or score > row["score"]
    if improved:
        db._conn.execute(
            "INSERT INTO game_scores (game, user_id, username, score, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(game, user_id) DO UPDATE SET "
            "score = excluded.score, username = excluded.username, "
            "updated_at = excluded.updated_at",
            (body.game, user["id"], user["username"], score, int(time.time() * 1000)))
        db._conn.commit()
    best = score if improved else row["score"]
    return {"ok": True, "improved": improved, "best": best}
