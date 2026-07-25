// apps/gaminghub.js
// -----------------
// Gaming Hub: EIN Fenster für alle Spiele. Zeigt eine Übersicht (Kacheln)
// und rendert das gewählte Spiel direkt im selben Fenster (mit Zurück-Button).
// Neue Spiele werden einfach in GAMES registriert.
import { renderTowerDefense } from "./towerdefense.js";
import { renderGameOfLife } from "./gameoflife.js";
import { renderWordle, renderSudoku, renderSnake, renderMinesweeper, renderTetris } from "./minigames.js";
import { api } from "../api.js";
import { esc } from "../utils.js";
import { t } from "../i18n.js";

// Score still ans Backend melden (gespeichert wird nur der persönliche
// Bestwert pro Spiel). Wird von den Spielen aufgerufen; Fehler (z.B. offline)
// werden ignoriert, ein NEUER Bestwert wird gefeiert.
export function reportScore(game, score) {
  if (!Number.isFinite(score) || score <= 0) return;
  api.submitGameScore(game, Math.round(score)).then((res) => {
    if (res?.improved) {
      window.notify?.(`🏆 Neuer persönlicher Bestwert: ${res.best} Punkte!`, "success", 4000);
    }
  }).catch(() => {});
}

const GAMES = [
  {
    id: "towerdefense", icon: "🛡️", title: "Tower Defense",
    desc: t("u_verteidige_den_server_auf_4_maps_f"),
    render: renderTowerDefense,
  },
  {
    id: "gameoflife", icon: "🧬", title: "Game of Life",
    desc: t("u_john_conways_zell_automat_muster_z"),
    render: renderGameOfLife,
  },
  {
    id: "wordle", icon: "🟩", title: "Wordle des Tages",
    desc: t("u_errate_das_deutsche_5_buchstaben_w"),
    render: renderWordle,
  },
  {
    id: "sudoku", icon: "🔢", title: "Sudoku",
    desc: t("u_zahlenratsel_mit_generator_in_drei"),
    render: renderSudoku,
  },
  {
    id: "snake", icon: "🐍", title: "Snake",
    desc: t("u_der_terminal_klassiker_apfel_fress"),
    render: renderSnake,
  },
  {
    id: "minesweeper", icon: "💣", title: "Minesweeper",
    desc: t("u_minenfeld_raumen_in_drei_gro_en_li"),
    render: renderMinesweeper,
  },
  {
    id: "tetris", icon: "🧱", title: "Tetris",
    desc: t("u_fallende_blocke_stapeln_reihen_rau"),
    render: renderTetris,
  },
];

export function renderGamingHub(body, win) {
  function showMenu() {
    body.innerHTML = `
      <div style="display:flex;flex-direction:column;height:100%;background:var(--panel)">
        <div style="padding:16px 18px 6px;display:flex;align-items:flex-start;gap:12px">
          <div style="flex:1">
            <h2 style="margin:0 0 4px">🎮 Gaming Hub</h2>
            <p style="color:var(--subtext);font-size:13px;margin:0">
              Kurze Pause verdient? Alle Spiele an einem Ort.
            </p>
          </div>
          <button class="taskbar-btn" id="gh-scores" style="font-size:13px">🏆 Scoreboard</button>
        </div>
        <div style="flex:1;overflow:auto;padding:12px 18px;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;align-content:start">
          ${GAMES.map((g) => `
            <button data-game="${g.id}" style="text-align:left;background:var(--panel-2);border:1px solid var(--border);border-radius:10px;padding:14px;cursor:pointer;color:var(--text)">
              <div style="font-size:30px;margin-bottom:6px">${g.icon}</div>
              <div style="font-weight:700;margin-bottom:4px">${g.title}</div>
              <div style="color:var(--subtext);font-size:12px;line-height:1.4">${g.desc}</div>
            </button>
          `).join("")}
        </div>
      </div>
    `;
    body.querySelectorAll("[data-game]").forEach((btn) => {
      btn.addEventListener("click", () => showGame(btn.dataset.game));
    });
    body.querySelector("#gh-scores").addEventListener("click", showScoreboard);
  }

  // ---------------- Scoreboard (Top-Score pro Benutzer, pro Spiel) ----------------
  async function showScoreboard() {
    body.innerHTML = `
      <div style="display:flex;flex-direction:column;height:100%;background:var(--panel)">
        <div style="display:flex;align-items:center;gap:10px;padding:6px 10px;background:var(--panel-2);border-bottom:1px solid var(--border)">
          <button class="taskbar-btn" id="gh-back" style="font-size:12px">← Gaming Hub</button>
          <span style="font-size:13px;font-weight:700">🏆 Scoreboard</span>
          <span style="flex:1"></span>
          <button class="taskbar-btn" id="gh-refresh" style="font-size:12px">🔄</button>
        </div>
        <div id="gh-sc" style="flex:1;overflow:auto;padding:14px 18px;display:grid;
             grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;align-content:start">
          <div style="color:var(--subtext)">Lade Bestenlisten…</div>
        </div>
      </div>`;
    body.querySelector("#gh-back").addEventListener("click", showMenu);
    body.querySelector("#gh-refresh").addEventListener("click", showScoreboard);
    const scEl = body.querySelector("#gh-sc");
    try {
      const data = await api.gameScores();
      const MEDALS = ["🥇", "🥈", "🥉"];
      scEl.innerHTML = Object.entries(data).map(([game, info]) => `
        <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:10px;padding:12px">
          <div style="font-weight:700;margin-bottom:8px">${esc(info.label)}</div>
          ${info.entries.length ? `
            <table style="width:100%;font-size:12px;border-collapse:collapse">
              ${info.entries.map((e, i) => `
                <tr style="${i % 2 ? "background:rgba(255,255,255,.03)" : ""}">
                  <td style="padding:3px 6px;width:28px">${MEDALS[i] || `${i + 1}.`}</td>
                  <td style="padding:3px 6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:140px">${esc(e.username)}</td>
                  <td style="padding:3px 6px;text-align:right;font-weight:700;font-variant-numeric:tabular-nums">${e.score}</td>
                </tr>`).join("")}
            </table>`
          : `<div style="color:var(--subtext);font-size:12px">Noch keine Einträge - sei der Erste! 🎯</div>`}
        </div>`).join("");
    } catch (e) {
      scEl.innerHTML = `<div style="color:var(--danger)">Scoreboard nicht ladbar: ${esc(e.message)}<br>
        <span style="color:var(--subtext);font-size:12px">Läuft das Backend mit games_routes.py?</span></div>`;
    }
  }

  function showGame(id) {
    const game = GAMES.find((g) => g.id === id);
    if (!game) return;
    body.innerHTML = `
      <div style="display:flex;flex-direction:column;height:100%">
        <div style="display:flex;align-items:center;gap:10px;padding:6px 10px;background:var(--panel-2);border-bottom:1px solid var(--border)">
          <button class="taskbar-btn" id="gh-back" style="font-size:12px">← Gaming Hub</button>
          <span style="font-size:13px;font-weight:700">${game.icon} ${game.title}</span>
        </div>
        <div id="gh-game" style="flex:1;position:relative;overflow:hidden"></div>
      </div>
    `;
    body.querySelector("#gh-back").addEventListener("click", showMenu);
    // Das Spiel rendert in den eigenen Container (gleiche Signatur wie Apps).
    game.render(body.querySelector("#gh-game"), win);
  }

  showMenu();
}
