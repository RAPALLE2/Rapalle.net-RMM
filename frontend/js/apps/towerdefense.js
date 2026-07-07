// apps/towerdefense.js
// --------------------
// Kleines Tower-Defense-Spiel mit IT-Security-Thema (Easter Egg).
// Man platziert "Firewalls" und "Antivirus"-Türme, um anrückende Malware
// (Viren, Trojaner, Würmer) aufzuhalten, bevor sie den Server erreichen.
// Reines Canvas-Spiel, keine Backend-Anbindung.

export function renderTowerDefense(body, win) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:#0a1420">
      <div style="display:flex;gap:12px;align-items:center;padding:8px 12px;background:var(--panel-2);font-size:13px;flex-wrap:wrap">
        <span>🛡️ System-Integrität: <b id="td-hp">100</b>%</span>
        <span>💰 Budget: <b id="td-money">150</b></span>
        <span>🌊 Welle: <b id="td-wave">0</b></span>
        <span style="flex:1"></span>
        <button class="taskbar-btn" id="td-tower-fw" title="Firewall: 50">🧱 Firewall (50)</button>
        <button class="taskbar-btn" id="td-tower-av" title="Antivirus: 90">🦠 Antivirus (90)</button>
        <button class="taskbar-btn" id="td-start">▶ Welle starten</button>
      </div>
      <div style="flex:1;position:relative;overflow:hidden">
        <canvas id="td-canvas" style="display:block;width:100%;height:100%"></canvas>
        <div id="td-overlay" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;background:rgba(10,20,32,0.85);text-align:center;padding:20px">
          <h2 style="color:#ff4d6d;margin:0 0 8px">🛡️ RAPALLE Defense</h2>
          <p style="color:#9fb3c8;max-width:400px;font-size:13px">
            Schütze deinen Server vor Malware! Wähle oben einen Turm, klicke aufs Spielfeld
            zum Platzieren, dann starte die Welle. Türme feuern automatisch.
          </p>
          <button class="btn-primary" id="td-begin" style="margin-top:12px">Spiel starten</button>
        </div>
      </div>
    </div>
  `;

  const canvas = body.querySelector("#td-canvas");
  const ctx = canvas.getContext("2d");
  const hpEl = body.querySelector("#td-hp");
  const moneyEl = body.querySelector("#td-money");
  const waveEl = body.querySelector("#td-wave");
  const overlay = body.querySelector("#td-overlay");

  // Spielzustand
  let W = 700, H = 480;
  let hp = 100, money = 150, wave = 0;
  let placing = null;          // "fw" | "av" | null
  let towers = [];             // {x,y,type,range,dmg,cooldown,timer}
  let enemies = [];            // {x,y,hp,maxHp,speed,reward,type}
  let projectiles = [];        // {x,y,tx,ty,target,dmg}
  let running = false;
  let spawnQueue = 0, spawnTimer = 0;
  let animId = null;

  // Der Pfad, den die Malware entlangläuft (Wegpunkte, relativ zur Canvas-Größe)
  const pathRel = [
    [0.0, 0.2], [0.3, 0.2], [0.3, 0.6], [0.65, 0.6], [0.65, 0.3], [1.0, 0.3],
  ];
  function pathPoints() {
    return pathRel.map(([rx, ry]) => [rx * W, ry * H]);
  }

  function resize() {
    const rect = canvas.getBoundingClientRect();
    W = canvas.width = Math.max(400, rect.width);
    H = canvas.height = Math.max(300, rect.height);
  }

  // --- Türme platzieren ---
  body.querySelector("#td-tower-fw").addEventListener("click", () => { 
    if (!placing) {
      placing = "fw"; 
      return;
    } else {
      if (placing === "av") {
        placing = "fw";
        return;
      } else {
        placing = null;
        return;
      }
    }
  });

  body.querySelector("#td-tower-av").addEventListener("click", () => { 
    if (!placing) {
      placing = "av"; 
      return;
    } else {
      if (placing === "fw") {
        placing = "av";
        return;
      } else {
        placing = null;
        return;
      }
    }
  });

  canvas.addEventListener("click", (e) => {
    if (!placing) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const cost = placing === "fw" ? 50 : 90;
    if (money < cost) { flash("Nicht genug Budget!"); return; }

    // Nicht direkt auf den Pfad bauen
    if (nearPath(x, y, 26)) { flash("Zu nah am Pfad!"); return; }

    if (placing === "fw") {
      towers.push({ x, y, type: "fw", range: 90, dmg: 8, cooldown: 30, timer: 0, color: "#4da6ff" });
    } else {
      towers.push({ x, y, type: "av", range: 130, dmg: 20, cooldown: 55, timer: 0, color: "#3ecf8e" });
    }
    money -= cost;
    updateHud();
  });

  function nearPath(x, y, dist) {
    const pts = pathPoints();
    for (let i = 0; i < pts.length - 1; i++) {
      if (distToSegment(x, y, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) < dist) return true;
    }
    return false;
  }

  function distToSegment(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    const len2 = dx * dx + dy * dy || 1;
    let t = ((px - x1) * dx + (py - y1) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const cx = x1 + t * dx, cy = y1 + t * dy;
    return Math.hypot(px - cx, py - cy);
  }

  let flashMsg = "", flashTimer = 0;
  function flash(msg) { flashMsg = msg; flashTimer = 90; }

  // --- Welle starten ---
  function startWave() {
    if (running && spawnQueue > 0) return;
    wave++;
    spawnQueue = 4 + wave * 2;      // immer mehr Gegner
    spawnTimer = 0;
    running = true;
    updateHud();
  }
  body.querySelector("#td-start").addEventListener("click", startWave);

  const MALWARE_TYPES = [
    { type: "Virus", emoji: "🦠", hp: 30, speed: 1.0, reward: 15, color: "#ff6b6b" },
    { type: "Trojaner", emoji: "🐴", hp: 60, speed: 0.7, reward: 25, color: "#c77dff" },
    { type: "Wurm", emoji: "🪱", hp: 20, speed: 1.6, reward: 12, color: "#ffd166" },
    { type: "RAT", emoji: "🐀", hp: 90, speed: 0.6, reward: 35, color: "#ef476f" },
  ];

  function spawnEnemy() {
    // Mit steigender Welle stärkere Malware
    const pool = MALWARE_TYPES.slice(0, Math.min(MALWARE_TYPES.length, 1 + Math.floor(wave / 2)));
    const t = pool[Math.floor(Math.random() * pool.length)];
    const hpScaled = t.hp + wave * 8;
    enemies.push({
      seg: 0, t: 0,
      x: 0, y: 0,
      hp: hpScaled, maxHp: hpScaled,
      speed: t.speed, reward: t.reward,
      emoji: t.emoji, color: t.color,
    });
  }

  function updateHud() {
    hpEl.textContent = Math.max(0, Math.round(hp));
    moneyEl.textContent = money;
    waveEl.textContent = wave;
  }

  // --- Gegner entlang des Pfades bewegen ---
  function moveEnemy(e) {
    const pts = pathPoints();
    if (e.seg >= pts.length - 1) return true; // Ziel erreicht
    const [x1, y1] = pts[e.seg];
    const [x2, y2] = pts[e.seg + 1];
    const segLen = Math.hypot(x2 - x1, y2 - y1);
    e.t += (e.speed * 1.4) / segLen;
    if (e.t >= 1) { e.seg++; e.t = 0; }
    e.x = x1 + (x2 - x1) * e.t;
    e.y = y1 + (y2 - y1) * e.t;
    return false;
  }

  // --- Hauptschleife ---
  function loop() {
    ctx.clearRect(0, 0, W, H);
    drawPath();

    // Gegner spawnen
    if (running && spawnQueue > 0) {
      spawnTimer--;
      if (spawnTimer <= 0) { spawnEnemy(); spawnQueue--; spawnTimer = 40; }
    }

    // Gegner bewegen
    for (const e of enemies) {
      const reached = moveEnemy(e);
      if (reached) { e.dead = true; hp -= 10; flash("⚠ Malware durchgebrochen!"); updateHud(); }
    }

    // Türme feuern
    for (const tw of towers) {
      tw.timer--;
      if (tw.timer <= 0) {
        const target = enemies.find((e) => !e.dead && Math.hypot(e.x - tw.x, e.y - tw.y) <= tw.range);
        if (target) {
          projectiles.push({ x: tw.x, y: tw.y, target, dmg: tw.dmg, color: tw.color });
          tw.timer = tw.cooldown;
        }
      }
    }

    // Projektile bewegen
    for (const p of projectiles) {
      if (p.target.dead) { p.dead = true; continue; }
      const dx = p.target.x - p.x, dy = p.target.y - p.y;
      const d = Math.hypot(dx, dy);
      if (d < 8) {
        p.target.hp -= p.dmg;
        p.dead = true;
        if (p.target.hp <= 0 && !p.target.dead) {
          p.target.dead = true;
          money += p.target.reward;
          updateHud();
        }
      } else {
        p.x += (dx / d) * 8;
        p.y += (dy / d) * 8;
      }
    }

    // Zeichnen
    drawTowers();
    drawEnemies();
    drawProjectiles();
    drawServer();

    // Aufräumen
    enemies = enemies.filter((e) => !e.dead);
    projectiles = projectiles.filter((p) => !p.dead);

    // Welle zu Ende?
    if (running && spawnQueue === 0 && enemies.length === 0) {
      running = false;
      money += 30; // Bonus für überstandene Welle
      flash(`✓ Welle ${wave} überstanden! +30 Budget`);
      updateHud();
    }

    // Flash-Nachricht
    if (flashTimer > 0) {
      flashTimer--;
      ctx.fillStyle = "rgba(255,255,255,0.9)";
      ctx.font = "bold 18px system-ui";
      ctx.textAlign = "center";
      ctx.fillText(flashMsg, W / 2, 40);
    }

    // Game Over?
    if (hp <= 0) {
      gameOver();
      return;
    }

    animId = requestAnimationFrame(loop);
  }

  function drawPath() {
    const pts = pathPoints();
    ctx.strokeStyle = "#1c2e44";
    ctx.lineWidth = 34;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.stroke();
    // gestrichelte Mittellinie
    ctx.strokeStyle = "#33507044";
    ctx.lineWidth = 2;
    ctx.setLineDash([8, 10]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function drawTowers() {
    for (const tw of towers) {
      // Reichweite (dezent)
      ctx.fillStyle = tw.color + "12";
      ctx.beginPath();
      ctx.arc(tw.x, tw.y, tw.range, 0, Math.PI * 2);
      ctx.fill();
      // Turm
      ctx.fillStyle = tw.color;
      ctx.beginPath();
      ctx.arc(tw.x, tw.y, 14, 0, Math.PI * 2);
      ctx.fill();
      ctx.font = "16px system-ui";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(tw.type === "fw" ? "🧱" : "🛡️", tw.x, tw.y);
    }
  }

  function drawEnemies() {
    for (const e of enemies) {
      ctx.font = "22px system-ui";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(e.emoji, e.x, e.y);
      // HP-Balken
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

  function gameOver() {
    running = false;
    overlay.style.display = "flex";
    overlay.innerHTML = `
      <h2 style="color:#ff4d6d;margin:0 0 8px">💀 FATAL: DATA BREACH OCCURRED</h2>
      <p style="color:#9fb3c8;font-size:13px">
        Die Malware hat deinen Server kompromittiert. Notabschaltung eingeleitet.<br>
        Du hast <b>${wave}</b> Wellen überstanden.
      </p>
      <button class="btn-primary" id="td-restart" style="margin-top:12px">Neu starten</button>
    `;
    overlay.querySelector("#td-restart").addEventListener("click", resetGame);
  }

  function resetGame() {
    hp = 100; money = 150; wave = 0;
    towers = []; enemies = []; projectiles = [];
    running = false; spawnQueue = 0; placing = null;
    overlay.style.display = "none";
    updateHud();
    if (!animId) loop();
  }

  // --- Spielstart ---
  function begin() {
    overlay.style.display = "none";
    resize();
    updateHud();
    if (animId) cancelAnimationFrame(animId);
    loop();
  }
  body.querySelector("#td-begin").addEventListener("click", begin);

  // Canvas an Fenstergröße anpassen
  setTimeout(resize, 50);
}
