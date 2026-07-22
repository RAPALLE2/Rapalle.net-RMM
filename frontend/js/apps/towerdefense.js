// apps/towerdefense.js
// --------------------
// Tower-Defense-Spiel mit IT-Security-Thema (Teil des Gaming Hubs).
//
// NEU: Raster-System (Türme rasten auf Zellen ein, Pfad belegt Zellen) und
// mehrere Maps/Level mit unterschiedlichen Pfaden.
//
// Türme (alle bis Stufe 5 aufrüstbar):
//   🧱 Firewall   50$  - blockt Threads: solide Einzelziel-Schüsse
//   🔎 IDS        75$  - schnelle Erkennung: sehr hohe Feuerrate, große Reichweite
//   🦠 Antivirus 100$  - High-Damage-Scan: langsam, aber richtig viel Schaden
//   ☣️ Quarantäne 150$ - isoliert MEHRERE Gegner gleichzeitig (Flächenschaden)
//
// Upgrade-Kosten = Basispreis × 50% × aktuelle Stufe.
// Verkauf erstattet 70% der GESAMT investierten Summe.
// Gegner: 🐛 Bugs (niedrigste Werte), ⚠️ Threats (mittel), 🦠 Viren (stark).

// ---- Maps: Pfade in RASTER-Koordinaten (logisches Gitter 20 x 13 Zellen) ----
import { reportScore } from "./gaminghub.js";

const GRID_COLS = 20, GRID_ROWS = 13;
const MAPS = [
  {
    id: "serverraum", name: "Serverraum", icon: "🖥️",
    desc: "Der Klassiker: eine S-Kurve durchs Rechenzentrum.",
    path: [[0, 2], [6, 2], [6, 8], [13, 8], [13, 4], [19, 4]],
  },
  {
    id: "zickzack", name: "Zickzack-Backbone", icon: "⚡",
    desc: "Vier enge Kurven - perfekt für Quarantäne-Flächen.",
    path: [[0, 1], [16, 1], [16, 4], [3, 4], [3, 7], [16, 7], [16, 10], [0, 10], [0, 12], [19, 12]],
  },
  {
    id: "spirale", name: "Malware-Spirale", icon: "🌀",
    desc: "Langer Spiralweg ins Zentrum - viel Zeit zum Scannen.",
    path: [[0, 0], [18, 0], [18, 11], [2, 11], [2, 3], [15, 3], [15, 8], [5, 8], [5, 5], [19, 5]],
  },
  {
    id: "autobahn", name: "Daten-Autobahn", icon: "🛣️",
    desc: "Kurzer, direkter Weg - hier zählt Feuerkraft, nicht Zeit.",
    path: [[0, 6], [8, 6], [8, 3], [12, 3], [12, 9], [19, 9]],
  },
];

export function renderTowerDefense(body, win) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:#0a1420">
      <div style="display:flex;gap:10px;align-items:center;padding:8px 12px;background:var(--panel-2);font-size:13px;flex-wrap:wrap">
        <span id="td-mapname" style="font-weight:700"></span>
        <span>🛡️ <b id="td-hp">100</b>%</span>
        <span>💰 <b id="td-money">200</b>$</span>
        <span>🌊 Welle: <b id="td-wave">0</b></span>
        <span style="flex:1"></span>
        <button class="taskbar-btn" data-tower="fw" title="Firewall: blockt Threads (solide Einzelschüsse)">🧱 50$</button>
        <button class="taskbar-btn" data-tower="ids" title="IDS: schnelle Erkennung (hohe Feuerrate, große Reichweite)">🔎 75$</button>
        <button class="taskbar-btn" data-tower="av" title="Antivirus: High-Damage-Scan">🦠 100$</button>
        <button class="taskbar-btn" data-tower="qr" title="Quarantäne: isoliert mehrere Gegner (Flächenschaden)">☣️ 150$</button>
        <button class="taskbar-btn" id="td-start">▶ Welle</button>
        <button class="taskbar-btn" id="td-maps" title="Andere Map wählen">🗺️</button>
      </div>
      <div style="flex:1;position:relative;overflow:hidden">
        <canvas id="td-canvas" style="display:block;width:100%;height:100%"></canvas>
        <div id="td-tinfo" style="position:absolute;right:10px;top:10px;display:none;background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:10px 12px;font-size:12px;min-width:190px"></div>
        <div id="td-overlay" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;background:rgba(10,20,32,0.9);text-align:center;padding:20px;overflow:auto"></div>
      </div>
    </div>
  `;

  const canvas = body.querySelector("#td-canvas");
  const ctx = canvas.getContext("2d");
  const hpEl = body.querySelector("#td-hp");
  const moneyEl = body.querySelector("#td-money");
  const waveEl = body.querySelector("#td-wave");
  const mapNameEl = body.querySelector("#td-mapname");
  const overlay = body.querySelector("#td-overlay");
  const tinfo = body.querySelector("#td-tinfo");

  const TOWER_TYPES = {
    fw:  { name: "Firewall",   emoji: "🧱", cost: 50,  range: 95,  dmg: 10, cooldown: 32, color: "#4da6ff", aoe: 0 },
    ids: { name: "IDS",        emoji: "🔎", cost: 75,  range: 150, dmg: 6,  cooldown: 12, color: "#ffd166", aoe: 0 },
    av:  { name: "Antivirus",  emoji: "🦠", cost: 100, range: 120, dmg: 34, cooldown: 60, color: "#3ecf8e", aoe: 0 },
    qr:  { name: "Quarantäne", emoji: "☣️", cost: 150, range: 110, dmg: 14, cooldown: 45, color: "#c77dff", aoe: 55 },
  };
  const MAX_LEVEL = 5;
  function statsFor(type, level) {
    const b = TOWER_TYPES[type];
    const f = level - 1;
    return {
      dmg: Math.round(b.dmg * Math.pow(1.25, f)),
      range: Math.round(b.range * Math.pow(1.08, f)),
      cooldown: Math.max(5, Math.round(b.cooldown * Math.pow(0.94, f))),
      aoe: b.aoe ? Math.round(b.aoe * Math.pow(1.08, f)) : 0,
    };
  }
  function upgradeCost(t) { return Math.round(TOWER_TYPES[t.type].cost * 0.5 * t.level); }
  function refundValue(t) { return Math.round(t.invested * 0.7); }

  const ENEMY_TYPES = [
    { key: "bug",    name: "Bug",    emoji: "🐛", hp: 18,  speed: 0.55, dmg: 4,  reward: 8,  minWave: 1 },
    { key: "threat", name: "Threat", emoji: "⚠️", hp: 45,  speed: 0.95, dmg: 8,  reward: 16, minWave: 2 },
    { key: "virus",  name: "Virus",  emoji: "🦠", hp: 90,  speed: 1.25, dmg: 14, reward: 28, minWave: 4 },
  ];

  // ---- Zustand ----
  let W = 700, H = 480;
  let cell = 34;                       // Pixelgröße einer Rasterzelle (dynamisch)
  let offX = 0, offY = 0;              // Zentrier-Offset des Rasters
  let map = MAPS[0];
  let pathCells = new Set();           // "x,y" - vom Pfad belegte Zellen
  let hp = 100, money = 200, wave = 0;
  let placing = null, selected = null;
  let hoverCell = null;                // {gx,gy} unter der Maus
  let towers = [];                     // + gx,gy (Rasterzelle)
  let enemies = [], projectiles = [];
  let running = false;
  let spawnQueue = [], spawnTimer = 0;
  let animId = null;

  // ---- Raster-Helfer ----
  const cellCenter = (gx, gy) => [offX + gx * cell + cell / 2, offY + gy * cell + cell / 2];
  const cellAt = (px, py) => {
    const gx = Math.floor((px - offX) / cell), gy = Math.floor((py - offY) / cell);
    return (gx >= 0 && gy >= 0 && gx < GRID_COLS && gy < GRID_ROWS) ? { gx, gy } : null;
  };
  const pathPoints = () => map.path.map(([gx, gy]) => cellCenter(gx, gy));

  // Alle Zellen entlang der Pfad-Segmente als belegt markieren.
  function computePathCells() {
    pathCells = new Set();
    const p = map.path;
    for (let i = 0; i < p.length - 1; i++) {
      const [x1, y1] = p[i], [x2, y2] = p[i + 1];
      const dx = Math.sign(x2 - x1), dy = Math.sign(y2 - y1);
      let x = x1, y = y1;
      pathCells.add(`${x},${y}`);
      while (x !== x2 || y !== y2) { x += dx; y += dy; pathCells.add(`${x},${y}`); }
    }
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    W = canvas.width = Math.max(400, rect.width);
    H = canvas.height = Math.max(300, rect.height);
    cell = Math.floor(Math.min(W / GRID_COLS, H / GRID_ROWS));
    offX = Math.floor((W - cell * GRID_COLS) / 2);
    offY = Math.floor((H - cell * GRID_ROWS) / 2);
    // Turm-Positionen ans neue Raster anpassen
    for (const t of towers) [t.x, t.y] = cellCenter(t.gx, t.gy);
  }

  // ---- Toolbar ----
  body.querySelectorAll("[data-tower]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const t = btn.dataset.tower;
      placing = placing === t ? null : t;
      selected = null; renderTowerInfo();
      body.querySelectorAll("[data-tower]").forEach((b) =>
        b.classList.toggle("btn-primary", b.dataset.tower === placing));
    });
  });
  body.querySelector("#td-maps").addEventListener("click", () => showMapSelect(false));

  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    hoverCell = cellAt(e.clientX - rect.left, e.clientY - rect.top);
  });
  canvas.addEventListener("mouseleave", () => { hoverCell = null; });

  canvas.addEventListener("click", (e) => {
    const rect = canvas.getBoundingClientRect();
    const c = cellAt(e.clientX - rect.left, e.clientY - rect.top);
    if (!c) return;

    if (!placing) {
      selected = towers.find((t) => t.gx === c.gx && t.gy === c.gy) || null;
      renderTowerInfo();
      return;
    }
    const base = TOWER_TYPES[placing];
    if (money < base.cost) { flash("Nicht genug Budget!"); return; }
    if (pathCells.has(`${c.gx},${c.gy}`)) { flash("Pfad-Zelle - hier läuft die Malware!"); return; }
    if (towers.some((t) => t.gx === c.gx && t.gy === c.gy)) { flash("Zelle schon belegt!"); return; }

    const [x, y] = cellCenter(c.gx, c.gy);
    const st = statsFor(placing, 1);
    towers.push({ x, y, gx: c.gx, gy: c.gy, type: placing, level: 1,
                  invested: base.cost, timer: 0, ...st,
                  color: base.color, emoji: base.emoji });
    money -= base.cost;
    updateHud();
  });

  // ---- Upgrade-/Verkaufs-Panel ----
  function renderTowerInfo() {
    if (!selected) { tinfo.style.display = "none"; return; }
    const t = selected;
    const base = TOWER_TYPES[t.type];
    const canUp = t.level < MAX_LEVEL;
    tinfo.style.display = "block";
    tinfo.innerHTML = `
      <div style="font-weight:700;margin-bottom:4px">${base.emoji} ${base.name} <span style="color:var(--subtext)">Stufe ${t.level}/${MAX_LEVEL}</span></div>
      <div style="color:var(--subtext)">Schaden: <b>${t.dmg}</b> · Reichweite: <b>${t.range}</b><br>
      Feuerrate: <b>${(60 / t.cooldown).toFixed(1)}/s</b>${t.aoe ? ` · Fläche: <b>${t.aoe}</b>` : ""}<br>
      Investiert: <b>${t.invested}$</b></div>
      <div style="display:flex;gap:6px;margin-top:8px">
        ${canUp
          ? `<button class="btn-primary" id="td-up" style="margin:0;width:auto;font-size:12px">⬆ Upgrade (${upgradeCost(t)}$)</button>`
          : `<span style="color:#3ecf8e;font-size:12px">★ Max. Stufe</span>`}
        <button class="taskbar-btn" id="td-sell" style="font-size:12px">💰 Verkaufen (${refundValue(t)}$)</button>
      </div>
    `;
    tinfo.querySelector("#td-up")?.addEventListener("click", () => {
      const c = upgradeCost(t);
      if (money < c) { flash("Nicht genug Budget fürs Upgrade!"); return; }
      money -= c; t.invested += c; t.level++;
      Object.assign(t, statsFor(t.type, t.level));
      updateHud(); renderTowerInfo();
    });
    tinfo.querySelector("#td-sell")?.addEventListener("click", () => {
      money += refundValue(t);
      towers = towers.filter((x) => x !== t);
      selected = null;
      updateHud(); renderTowerInfo();
    });
  }

  let flashMsg = "", flashTimer = 0;
  function flash(msg) { flashMsg = msg; flashTimer = 90; }

  // ---- Wellen ----
  function startWave() {
    if (running && spawnQueue.length > 0) return;
    wave++;
    spawnQueue = [];
    const total = 5 + wave * 2;
    for (let i = 0; i < total; i++) {
      const pool = ENEMY_TYPES.filter((t) => wave >= t.minWave);
      const weights = pool.map((t) => t.key === "bug" ? Math.max(1, 6 - wave)
                                    : t.key === "threat" ? 3 + wave * 0.5
                                    : 1 + wave * 0.7);
      const sum = weights.reduce((a, b) => a + b, 0);
      let r = Math.random() * sum;
      let pick = pool[0];
      for (let j = 0; j < pool.length; j++) { r -= weights[j]; if (r <= 0) { pick = pool[j]; break; } }
      spawnQueue.push(pick);
    }
    spawnTimer = 0;
    running = true;
    updateHud();
  }
  body.querySelector("#td-start").addEventListener("click", startWave);

  function spawnEnemy(t) {
    const hpScaled = Math.round(t.hp * (1 + wave * 0.22));
    enemies.push({ seg: 0, t: 0, x: 0, y: 0, hp: hpScaled, maxHp: hpScaled,
                   speed: t.speed, reward: t.reward, dmg: t.dmg, emoji: t.emoji });
  }

  function updateHud() {
    hpEl.textContent = Math.max(0, Math.round(hp));
    moneyEl.textContent = money;
    waveEl.textContent = wave;
    mapNameEl.textContent = `${map.icon} ${map.name}`;
  }

  function moveEnemy(e) {
    const pts = pathPoints();
    if (e.seg >= pts.length - 1) return true;
    const [x1, y1] = pts[e.seg];
    const [x2, y2] = pts[e.seg + 1];
    const segLen = Math.hypot(x2 - x1, y2 - y1);
    e.t += (e.speed * 1.4) / segLen;
    if (e.t >= 1) { e.seg++; e.t = 0; }
    e.x = x1 + (x2 - x1) * e.t;
    e.y = y1 + (y2 - y1) * e.t;
    return false;
  }

  function killEnemy(e) {
    if (e.dead) return;
    e.dead = true;
    money += e.reward;
    updateHud();
  }

  // ---- Hauptschleife ----
  function loop() {
    ctx.clearRect(0, 0, W, H);
    drawGrid();
    drawPath();

    if (running && spawnQueue.length > 0) {
      spawnTimer--;
      if (spawnTimer <= 0) { spawnEnemy(spawnQueue.shift()); spawnTimer = Math.max(18, 42 - wave); }
    }

    for (const e of enemies) {
      if (moveEnemy(e)) {
        e.dead = true; hp -= e.dmg;
        flash("⚠ Durchbruch! -" + e.dmg + "% Integrität");
        updateHud();
      }
    }

    for (const tw of towers) {
      tw.timer--;
      if (tw.timer <= 0) {
        const target = enemies.find((e) => !e.dead && Math.hypot(e.x - tw.x, e.y - tw.y) <= tw.range);
        if (target) {
          projectiles.push({ x: tw.x, y: tw.y, target, dmg: tw.dmg, aoe: tw.aoe, color: tw.color });
          tw.timer = tw.cooldown;
        }
      }
    }

    for (const p of projectiles) {
      if (p.target.dead) { p.dead = true; continue; }
      const dx = p.target.x - p.x, dy = p.target.y - p.y;
      const d = Math.hypot(dx, dy);
      if (d < 8) {
        p.dead = true;
        if (p.aoe) {
          const cx = p.target.x, cy = p.target.y;
          for (const e of enemies) {
            if (!e.dead && Math.hypot(e.x - cx, e.y - cy) <= p.aoe) {
              e.hp -= p.dmg;
              if (e.hp <= 0) killEnemy(e);
            }
          }
          ctx.fillStyle = p.color + "44";
          ctx.beginPath(); ctx.arc(cx, cy, p.aoe, 0, Math.PI * 2); ctx.fill();
        } else {
          p.target.hp -= p.dmg;
          if (p.target.hp <= 0) killEnemy(p.target);
        }
      } else {
        p.x += (dx / d) * 9;
        p.y += (dy / d) * 9;
      }
    }

    drawTowers();
    drawEnemies();
    drawProjectiles();
    drawServer();
    drawHoverCell();

    enemies = enemies.filter((e) => !e.dead);
    projectiles = projectiles.filter((p) => !p.dead);

    if (running && spawnQueue.length === 0 && enemies.length === 0) {
      running = false;
      const bonus = 30 + wave * 5;
      money += bonus;
      flash(`✓ Welle ${wave} überstanden! +${bonus}$`);
      updateHud();
    }

    if (flashTimer > 0) {
      flashTimer--;
      ctx.fillStyle = "rgba(255,255,255,0.9)";
      ctx.font = "bold 18px system-ui";
      ctx.textAlign = "center";
      ctx.fillText(flashMsg, W / 2, 40);
    }

    if (hp <= 0) { gameOver(); return; }
    animId = requestAnimationFrame(loop);
  }

  // ---- Zeichnen ----
  function drawGrid() {
    ctx.strokeStyle = "#14243a";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= GRID_COLS; x++) {
      ctx.moveTo(offX + x * cell + 0.5, offY);
      ctx.lineTo(offX + x * cell + 0.5, offY + GRID_ROWS * cell);
    }
    for (let y = 0; y <= GRID_ROWS; y++) {
      ctx.moveTo(offX, offY + y * cell + 0.5);
      ctx.lineTo(offX + GRID_COLS * cell, offY + y * cell + 0.5);
    }
    ctx.stroke();
  }

  function drawHoverCell() {
    if (!placing || !hoverCell) return;
    const blocked = pathCells.has(`${hoverCell.gx},${hoverCell.gy}`)
      || towers.some((t) => t.gx === hoverCell.gx && t.gy === hoverCell.gy);
    ctx.fillStyle = blocked ? "rgba(255,77,109,0.28)" : "rgba(62,207,142,0.25)";
    ctx.fillRect(offX + hoverCell.gx * cell, offY + hoverCell.gy * cell, cell, cell);
    // Reichweiten-Vorschau
    if (!blocked) {
      const [cx, cy] = cellCenter(hoverCell.gx, hoverCell.gy);
      ctx.strokeStyle = TOWER_TYPES[placing].color + "77";
      ctx.beginPath();
      ctx.arc(cx, cy, statsFor(placing, 1).range, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  function drawPath() {
    // Pfad als Raster-Zellen einfärben + Mittellinie
    ctx.fillStyle = "#1c2e44";
    for (const key of pathCells) {
      const [gx, gy] = key.split(",").map(Number);
      ctx.fillRect(offX + gx * cell, offY + gy * cell, cell, cell);
    }
    const pts = pathPoints();
    ctx.strokeStyle = "#33507066";
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 10]);
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function drawTowers() {
    for (const tw of towers) {
      if (tw === selected) {
        ctx.fillStyle = tw.color + "1c";
        ctx.beginPath(); ctx.arc(tw.x, tw.y, tw.range, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = tw.color + "66";
        ctx.beginPath(); ctx.arc(tw.x, tw.y, tw.range, 0, Math.PI * 2); ctx.stroke();
      }
      // Turm-Sockel füllt die Zelle
      ctx.fillStyle = tw.color + "22";
      ctx.fillRect(offX + tw.gx * cell + 2, offY + tw.gy * cell + 2, cell - 4, cell - 4);
      ctx.fillStyle = tw.color;
      ctx.beginPath();
      ctx.arc(tw.x, tw.y, Math.min(14, cell * 0.38), 0, Math.PI * 2);
      ctx.fill();
      ctx.font = `${Math.min(16, cell * 0.45)}px system-ui`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(tw.emoji, tw.x, tw.y);
      ctx.font = "8px system-ui";
      ctx.fillStyle = "#ffd166";
      ctx.fillText("★".repeat(tw.level), tw.x, tw.y + Math.min(22, cell * 0.55));
    }
  }

  function drawEnemies() {
    for (const e of enemies) {
      ctx.font = "22px system-ui";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(e.emoji, e.x, e.y);
      const w = 26;
      ctx.fillStyle = "#000a";
      ctx.fillRect(e.x - w / 2, e.y - 20, w, 4);
      ctx.fillStyle = e.hp > e.maxHp * 0.3 ? "#3ecf8e" : "#ff4d6d";
      ctx.fillRect(e.x - w / 2, e.y - 20, w * (e.hp / e.maxHp), 4);
    }
  }

  function drawProjectiles() {
    for (const p of projectiles) {
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function drawServer() {
    const pts = pathPoints();
    const [ex, ey] = pts[pts.length - 1];
    ctx.font = "30px system-ui";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("🖥️", ex, ey);
  }

  // ---- Map-Auswahl / Game Over / Start ----
  function showMapSelect(initial) {
    overlay.style.display = "flex";
    overlay.innerHTML = `
      <h2 style="color:#4da6ff;margin:0 0 4px">🛡️ RAPALLE Defense</h2>
      <p style="color:#9fb3c8;max-width:460px;font-size:13px;margin:0 0 12px">
        ${initial ? "Wähle eine Map. " : "Map wechseln setzt das laufende Spiel zurück. "}
        Türme rasten auf dem Gitter ein - Pfad-Zellen sind gesperrt.
        Upgrades bis Stufe 5 (Kosten = Basispreis × 50% × Stufe), Verkauf = 70% der Investition.
      </p>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;max-width:560px;width:100%">
        ${MAPS.map((m) => `
          <button data-map="${m.id}" style="text-align:left;background:var(--panel-2);border:1px solid ${m.id === map.id ? "#4da6ff" : "var(--border)"};border-radius:10px;padding:12px;cursor:pointer;color:var(--text)">
            <div style="font-size:24px">${m.icon}</div>
            <div style="font-weight:700;margin:4px 0 2px">${m.name}</div>
            <div style="color:#9fb3c8;font-size:11px;line-height:1.35">${m.desc}</div>
          </button>`).join("")}
      </div>
      ${initial ? "" : `<button class="taskbar-btn" id="td-back" style="margin-top:12px">Abbrechen (weiterspielen)</button>`}
    `;
    overlay.querySelectorAll("[data-map]").forEach((b) =>
      b.addEventListener("click", () => {
        map = MAPS.find((m) => m.id === b.dataset.map) || MAPS[0];
        resetGame();
      }));
    overlay.querySelector("#td-back")?.addEventListener("click", () => {
      overlay.style.display = "none";
      if (!animId) loop();
    });
    if (animId) { cancelAnimationFrame(animId); animId = null; }
  }

  function gameOver() {
    running = false;
    if (animId) { cancelAnimationFrame(animId); animId = null; }
    reportScore("towerdefense", wave);   // Scoreboard: überstandene Wellen
    overlay.style.display = "flex";
    overlay.innerHTML = `
      <h2 style="color:#ff4d6d;margin:0 0 8px">💀 FATAL: DATA BREACH OCCURRED</h2>
      <p style="color:#9fb3c8;font-size:13px">
        ${map.icon} ${map.name}: Die Malware hat deinen Server kompromittiert.<br>
        Du hast <b>${wave}</b> Wellen überstanden.
      </p>
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn-primary" id="td-restart" style="margin:0;width:auto">Neu starten</button>
        <button class="taskbar-btn" id="td-other">🗺️ Andere Map</button>
      </div>
    `;
    overlay.querySelector("#td-restart").addEventListener("click", resetGame);
    overlay.querySelector("#td-other").addEventListener("click", () => showMapSelect(true));
  }

  function resetGame() {
    hp = 100; money = 200; wave = 0;
    towers = []; enemies = []; projectiles = [];
    running = false; spawnQueue = []; placing = null; selected = null;
    body.querySelectorAll("[data-tower]").forEach((b) => b.classList.remove("btn-primary"));
    renderTowerInfo();
    overlay.style.display = "none";
    resize();
    computePathCells();
    updateHud();
    if (animId) cancelAnimationFrame(animId);
    loop();
  }

  setTimeout(() => { resize(); computePathCells(); updateHud(); showMapSelect(true); }, 50);
}
