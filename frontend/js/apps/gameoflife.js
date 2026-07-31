// apps/gameoflife.js
// ------------------
// John Conways "Game of Life" (Teil des Gaming Hubs).
// Regeln: Eine lebende Zelle mit 2-3 Nachbarn überlebt; eine tote Zelle mit
// genau 3 Nachbarn wird geboren; alles andere stirbt/bleibt tot.
// Zeichnen per Klick/Ziehen, Start/Pause, Einzelschritt, Zufall, Löschen,
// Geschwindigkeits-Regler. Der Rand ist "verbunden" (Torus).
export function renderGameOfLife(body, win) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:#0a1420">
      <style>
        /* Hübscher Tempo-Regler (Gradient-Track + runder Daumen) */
        .gol-speed-wrap { display:flex;align-items:center;gap:8px;background:var(--panel);
          border:1px solid var(--border);border-radius:20px;padding:4px 12px }
        .gol-speed { -webkit-appearance:none;appearance:none;width:140px;height:6px;border-radius:3px;
          background:linear-gradient(90deg,#4da6ff,#3ecf8e,#ffd166);outline:none;cursor:pointer }
        .gol-speed::-webkit-slider-thumb { -webkit-appearance:none;appearance:none;width:16px;height:16px;
          border-radius:50%;background:#fff;border:3px solid #3ecf8e;box-shadow:0 0 6px #3ecf8e88;
          cursor:grab;transition:transform .1s }
        .gol-speed::-webkit-slider-thumb:hover { transform:scale(1.2) }
        .gol-speed::-moz-range-thumb { width:14px;height:14px;border-radius:50%;background:#fff;
          border:3px solid #3ecf8e;box-shadow:0 0 6px #3ecf8e88;cursor:grab }
        .gol-speed::-moz-range-track { height:6px;border-radius:3px;
          background:linear-gradient(90deg,#4da6ff,#3ecf8e,#ffd166) }
        .gol-speed-val { min-width:64px;text-align:center;font-variant-numeric:tabular-nums;
          font-weight:700;color:#3ecf8e;font-size:12px }
        .gol-speed-preset { font-size:11px;padding:1px 8px }
      </style>
      <div style="display:flex;gap:8px;align-items:center;padding:8px 12px;background:var(--panel-2);font-size:13px;flex-wrap:wrap">
        <button class="btn-primary" id="gol-play" style="margin:0;width:auto">▶ Start</button>
        <button class="taskbar-btn" id="gol-step">⏭ Schritt</button>
        <button class="taskbar-btn" id="gol-random">🎲 Zufall</button>
        <button class="taskbar-btn" id="gol-clear">🧹 ${t("delete")}</button>
        <div class="gol-speed-wrap" title="Simulationstempo">
          <span>🐢</span>
          <input type="range" class="gol-speed" id="gol-speed" min="1" max="30" value="10" />
          <span>🐇</span>
          <span class="gol-speed-val" id="gol-speed-val">10 Gen/s</span>
        </div>
        <button class="taskbar-btn gol-speed-preset" data-speed="2">Langsam</button>
        <button class="taskbar-btn gol-speed-preset" data-speed="10">Normal</button>
        <button class="taskbar-btn gol-speed-preset" data-speed="25">Turbo</button>
        <span style="flex:1"></span>
        <span style="color:var(--subtext)">Generation: <b id="gol-gen">0</b> · Zellen: <b id="gol-count">0</b></span>
      </div>
      <div style="flex:1;position:relative;overflow:hidden">
        <canvas id="gol-canvas" style="display:block;width:100%;height:100%;cursor:crosshair"></canvas>
      </div>
    </div>
  `;

  const canvas = body.querySelector("#gol-canvas");
  const ctx = canvas.getContext("2d");
  const genEl = body.querySelector("#gol-gen");
  const countEl = body.querySelector("#gol-count");
  const playBtn = body.querySelector("#gol-play");

  const CELL = 12;                // Pixel pro Zelle
  let cols = 60, rows = 40;
  let grid = new Uint8Array(cols * rows);
  let running = false;
  let generation = 0;
  let stepsPerSecond = 10;
  let lastStep = 0;
  let animId = null;
  let alive = 0;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(300, rect.width);
    canvas.height = Math.max(200, rect.height);
    const newCols = Math.max(10, Math.floor(canvas.width / CELL));
    const newRows = Math.max(10, Math.floor(canvas.height / CELL));
    if (newCols !== cols || newRows !== rows) {
      // Bestehendes Muster in die neue Größe übernehmen (oben links).
      const next = new Uint8Array(newCols * newRows);
      for (let y = 0; y < Math.min(rows, newRows); y++) {
        for (let x = 0; x < Math.min(cols, newCols); x++) {
          next[y * newCols + x] = grid[y * cols + x];
        }
      }
      cols = newCols; rows = newRows; grid = next;
    }
    draw();
  }

  function countAlive() {
    alive = 0;
    for (let i = 0; i < grid.length; i++) alive += grid[i];
    countEl.textContent = alive;
  }

  // Ein Simulationsschritt (Torus-Rand: gegenüberliegende Seiten verbunden).
  function step() {
    const next = new Uint8Array(cols * rows);
    for (let y = 0; y < rows; y++) {
      const yu = (y - 1 + rows) % rows, yd = (y + 1) % rows;
      for (let x = 0; x < cols; x++) {
        const xl = (x - 1 + cols) % cols, xr = (x + 1) % cols;
        const n =
          grid[yu * cols + xl] + grid[yu * cols + x] + grid[yu * cols + xr] +
          grid[y  * cols + xl] +                       grid[y  * cols + xr] +
          grid[yd * cols + xl] + grid[yd * cols + x] + grid[yd * cols + xr];
        const cell = grid[y * cols + x];
        next[y * cols + x] = (cell && (n === 2 || n === 3)) || (!cell && n === 3) ? 1 : 0;
      }
    }
    grid = next;
    generation++;
    genEl.textContent = generation;
    countAlive();
  }

  function draw() {
    ctx.fillStyle = "#0a1420";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // dezentes Gitter
    ctx.strokeStyle = "#16283c";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= cols; x++) { ctx.moveTo(x * CELL + 0.5, 0); ctx.lineTo(x * CELL + 0.5, rows * CELL); }
    for (let y = 0; y <= rows; y++) { ctx.moveTo(0, y * CELL + 0.5); ctx.lineTo(cols * CELL, y * CELL + 0.5); }
    ctx.stroke();
    // lebende Zellen
    ctx.fillStyle = "#3ecf8e";
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        if (grid[y * cols + x]) ctx.fillRect(x * CELL + 1, y * CELL + 1, CELL - 2, CELL - 2);
      }
    }
  }

  function loop(ts) {
    if (running && ts - lastStep >= 1000 / stepsPerSecond) {
      lastStep = ts;
      step();
    }
    draw();
    animId = requestAnimationFrame(loop);
  }

  // ---- Zeichnen per Klick/Ziehen (Linksklick setzt, mit Alt löscht) ----
  let drawing = false, drawValue = 1;
  function cellAt(e) {
    const rect = canvas.getBoundingClientRect();
    const x = Math.floor((e.clientX - rect.left) / CELL);
    const y = Math.floor((e.clientY - rect.top) / CELL);
    if (x < 0 || y < 0 || x >= cols || y >= rows) return null;
    return { x, y };
  }
  canvas.addEventListener("mousedown", (e) => {
    const c = cellAt(e);
    if (!c) return;
    drawing = true;
    // Erster Klick bestimmt, ob gezeichnet oder radiert wird (Toggle-Gefühl).
    drawValue = grid[c.y * cols + c.x] ? 0 : 1;
    grid[c.y * cols + c.x] = drawValue;
    countAlive();
  });
  canvas.addEventListener("mousemove", (e) => {
    if (!drawing) return;
    const c = cellAt(e);
    if (c) { grid[c.y * cols + c.x] = drawValue; countAlive(); }
  });
  window.addEventListener("mouseup", () => { drawing = false; });

  // ---- Steuerung ----
  playBtn.addEventListener("click", () => {
    running = !running;
    playBtn.textContent = running ? "⏸ Pause" : "▶ Start";
  });
  body.querySelector("#gol-step").addEventListener("click", () => { if (!running) step(); });
  body.querySelector("#gol-clear").addEventListener("click", () => {
    grid.fill(0); generation = 0;
    genEl.textContent = 0; countAlive();
  });
  body.querySelector("#gol-random").addEventListener("click", () => {
    for (let i = 0; i < grid.length; i++) grid[i] = Math.random() < 0.25 ? 1 : 0;
    generation = 0; genEl.textContent = 0; countAlive();
  });
  const speedEl = body.querySelector("#gol-speed");
  const speedValEl = body.querySelector("#gol-speed-val");
  function applySpeed(v) {
    stepsPerSecond = Math.max(1, Math.min(30, parseInt(v, 10) || 10));
    speedEl.value = stepsPerSecond;
    speedValEl.textContent = `${stepsPerSecond} Gen/s`;
  }
  speedEl.addEventListener("input", (e) => applySpeed(e.target.value));
  body.querySelectorAll(".gol-speed-preset").forEach((b) =>
    b.addEventListener("click", () => applySpeed(b.dataset.speed)));

  // Startmuster: ein Gleiter + ein Pulsar-artiger Block, damit sofort was passiert.
  function seed() {
    const put = (x, y) => { if (x >= 0 && y >= 0 && x < cols && y < rows) grid[y * cols + x] = 1; };
    // Gleiter
    put(2, 1); put(3, 2); put(1, 3); put(2, 3); put(3, 3);
    // kleines Oszillator-Kreuz in der Mitte
    const cx = Math.floor(cols / 2), cy = Math.floor(rows / 2);
    for (let i = -2; i <= 2; i++) { put(cx + i, cy); put(cx, cy + i); }
    countAlive();
  }

  setTimeout(() => { resize(); seed(); if (!animId) animId = requestAnimationFrame(loop); }, 50);
}
