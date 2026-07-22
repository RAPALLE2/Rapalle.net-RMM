// apps/minigames.js
// -----------------
// Sammlung kleiner Spiele für den Gaming Hub:
//   🟩 Wordle des Tages (deutsches Wörterrätsel, täglich dasselbe für alle)
//   🔢 Sudoku (Generator mit 3 Schwierigkeitsgraden)
//   🐍 Snake, 💣 Minesweeper, 🧱 Tetris (die Klassiker à la MobaXterm)
// Alle Spiele rendern in einen Container (gleiche Signatur wie Apps).
// Scores gehen ans Gaming-Hub-Scoreboard (bester Wert pro Benutzer).
import { reportScore } from "./gaminghub.js";

// ================= WORDLE =================
const WORDLE_WORDS = [
  "abgas","ampel","angel","anker","apfel","armee","atlas","bauch","bauer","beere",
  "besen","bibel","biene","birne","blatt","blech","blick","blitz","block","blume",
  "bluse","boden","bogen","bohne","borke","brand","brett","brief","brise","bruch",
  "buche","bulle","busch","chaos","dampf","datum","daune","decke","dorne","draht",
  "dreck","droge","druck","eimer","eisen","engel","enkel","erbse","esche","essig",
  "etage","fabel","fahne","falke","farbe","faser","feder","feige","felge","ferse",
  "fisch","fluch","folie","forst","frist","frost","fuchs","funke","gabel","gasse",
  "gebet","geier","geige","gelee","genie","geste","gicht","gilde","glanz","glied",
  "gnade","grill","gruft","gummi","gurke","hafen","hagel","hallo","harfe","haube",
  "hebel","hecke","heide","herde","hirse","hitze","honig","horde","hotel","humor",
  "insel","jacke","kabel","kader","kakao","kamin","kanal","kanne","karte","kasse",
  "katze","kegel","kekse","kelle","kette","kiste","klang","kleid","klima","knopf",
  "koala","kohle","kranz","kreis","krone","kugel","kunst","kurve","lachs","laden",
  "lager","lampe","lanze","larve","laube","leber","leder","lehre","leier","liebe",
  "linie","lippe","liste","lotse","lunge","magen","mappe","marke","maske","mauer",
  "messe","miete","milch","modem","monat","moose","motor","mumie","nabel","nacht",
  "nadel","nagel","nebel","nelke","niere","oasen","obhut","onkel","orgel","otter",
  "palme","panda","pauke","pedal","perle","pfahl","pfeil","pferd","pflug","pilot",
  "pilze","pizza","platz","polka","pumpe","puppe","quark","radio","rampe","ranke",
  "ratte","raupe","regal","regen","reihe","reise","rinde","robbe","roman","rosen",
  "runde","salat","salbe","sauna","schaf","schal","scham","schub","seele","segel",
  "sehne","seife","seile","socke","sohle","sonne","speck","spiel","spore","sport",
  "spurt","staat","stadt","stahl","stall","stamm","stern","stier","stirn","stoff",
  "stuhl","sturm","tafel","tanne","tante","tasse","taube","teich","tempo","tiger",
  "tinte","tisch","titel","tonne","traum","treue","truhe","tulpe","union","villa",
  "wache","waffe","wagen","walze","wange","wanne","watte","welle","werft","weste",
  "wiege","wiese","winde","wolke","wonne","worte","wurst","zange","zebra","zeile",
  "ziege","zirka","zunge","zwerg",
];

export function renderWordle(body) {
  // Tages-Wort: deterministisch aus dem Datum, damit ALLE heute dasselbe raten.
  const epoch = Math.floor(Date.now() / 86400000);
  const solution = WORDLE_WORDS[epoch % WORDLE_WORDS.length].toUpperCase();
  const wordSet = new Set(WORDLE_WORDS.map((w) => w.toUpperCase()));
  const ROWS = 6;
  let guesses = [];       // fertige Versuche (Strings)
  let current = "";
  let done = false;

  const KEY_ROWS = ["QWERTZUIOP", "ASDFGHJKL", "YXCVBNM"];

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;align-items:center;background:#0a1420;overflow:auto;padding:12px">
      <div style="font-size:13px;color:var(--subtext);margin-bottom:8px">
        🟩 Wordle des Tages (${new Date().toLocaleDateString("de-DE")}) - errate das deutsche 5-Buchstaben-Wort in 6 Versuchen
      </div>
      <div id="wd-grid" style="display:grid;grid-template-rows:repeat(${ROWS},1fr);gap:5px;margin-bottom:14px"></div>
      <div id="wd-msg" style="height:20px;font-size:13px;font-weight:700;color:#ffd166;margin-bottom:8px"></div>
      <div id="wd-kbd" style="display:flex;flex-direction:column;gap:5px;align-items:center"></div>
    </div>
  `;
  const gridEl = body.querySelector("#wd-grid");
  const msgEl = body.querySelector("#wd-msg");
  const kbdEl = body.querySelector("#wd-kbd");
  const keyState = {};   // Buchstabe -> 'good'|'near'|'bad'

  function evalGuess(guess) {
    // Zweipass wie im Original (doppelte Buchstaben korrekt behandeln)
    const res = Array(5).fill("bad");
    const remain = {};
    for (let i = 0; i < 5; i++) {
      if (guess[i] === solution[i]) res[i] = "good";
      else remain[solution[i]] = (remain[solution[i]] || 0) + 1;
    }
    for (let i = 0; i < 5; i++) {
      if (res[i] === "good") continue;
      if (remain[guess[i]] > 0) { res[i] = "near"; remain[guess[i]]--; }
    }
    return res;
  }
  const COLORS = { good: "#3ecf8e", near: "#ffd166", bad: "#33405a" };

  function drawGrid() {
    let html = "";
    for (let r = 0; r < ROWS; r++) {
      const guess = guesses[r] || (r === guesses.length ? current : "");
      const evald = guesses[r] ? evalGuess(guesses[r]) : null;
      html += `<div style="display:grid;grid-template-columns:repeat(5,44px);gap:5px">`;
      for (let c = 0; c < 5; c++) {
        const ch = guess[c] || "";
        const bg = evald ? COLORS[evald[c]] : (ch ? "#22334d" : "#152238");
        html += `<div style="width:44px;height:44px;display:flex;align-items:center;justify-content:center;
          font-weight:800;font-size:20px;border-radius:6px;background:${bg};
          border:2px solid ${ch && !evald ? "#4da6ff" : "transparent"};color:#fff">${ch}</div>`;
      }
      html += `</div>`;
    }
    gridEl.innerHTML = html;
  }

  function drawKbd() {
    kbdEl.innerHTML = KEY_ROWS.map((row, i) => `
      <div style="display:flex;gap:4px">
        ${i === 2 ? `<button data-k="ENTER" class="taskbar-btn" style="font-size:11px;padding:8px 10px">⏎</button>` : ""}
        ${row.split("").map((k) => `
          <button data-k="${k}" class="taskbar-btn" style="width:34px;padding:8px 0;font-weight:700;
            background:${keyState[k] ? COLORS[keyState[k]] : ""};color:${keyState[k] ? "#fff" : ""}">${k}</button>`).join("")}
        ${i === 2 ? `<button data-k="BACK" class="taskbar-btn" style="font-size:11px;padding:8px 10px">⌫</button>` : ""}
      </div>`).join("");
    kbdEl.querySelectorAll("[data-k]").forEach((b) =>
      b.addEventListener("click", () => press(b.dataset.k)));
  }

  function press(k) {
    if (done) return;
    msgEl.textContent = "";
    if (k === "BACK") current = current.slice(0, -1);
    else if (k === "ENTER") submit();
    else if (/^[A-Z]$/.test(k) && current.length < 5) current += k;
    drawGrid();
  }

  function submit() {
    if (current.length !== 5) { msgEl.textContent = "Zu kurz!"; return; }
    if (!wordSet.has(current)) { msgEl.textContent = "Nicht in der Wortliste"; return; }
    guesses.push(current);
    for (let i = 0; i < 5; i++) {
      const st = evalGuess(current)[i];
      const k = current[i];
      const rank = { bad: 0, near: 1, good: 2 };
      if (!keyState[k] || rank[st] > rank[keyState[k]]) keyState[k] = st;
    }
    if (current === solution) {
      done = true;
      msgEl.style.color = "#3ecf8e";
      msgEl.textContent = `🎉 Richtig in ${guesses.length}/6!`;
      reportScore("wordle", (7 - guesses.length) * 100);   // weniger Versuche = mehr Punkte
    } else if (guesses.length >= ROWS) {
      done = true;
      msgEl.style.color = "#ff4d6d";
      msgEl.textContent = `Das Wort war: ${solution}`;
    }
    current = "";
    drawKbd();
  }

  const onKey = (e) => {
    if (e.key === "Enter") press("ENTER");
    else if (e.key === "Backspace") press("BACK");
    else if (/^[a-zA-Z]$/.test(e.key)) press(e.key.toUpperCase());
  };
  body.tabIndex = 0;
  body.addEventListener("keydown", onKey);
  setTimeout(() => body.focus(), 60);

  drawGrid();
  drawKbd();
}

// ================= SUDOKU =================
export function renderSudoku(body) {
  let board = [], puzzle = [], fixed = [], selected = null;
  let difficulty = 40;   // Anzahl zu entfernender Zellen

  // Vollständiges, gültiges Board per Backtracking erzeugen.
  function generateFull() {
    const g = Array.from({ length: 9 }, () => Array(9).fill(0));
    const ok = (r, c, v) => {
      for (let i = 0; i < 9; i++) if (g[r][i] === v || g[i][c] === v) return false;
      const br = r - r % 3, bc = c - c % 3;
      for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++)
        if (g[br + i][bc + j] === v) return false;
      return true;
    };
    function fill(pos) {
      if (pos === 81) return true;
      const r = Math.floor(pos / 9), c = pos % 9;
      const nums = [1,2,3,4,5,6,7,8,9].sort(() => Math.random() - 0.5);
      for (const v of nums) {
        if (ok(r, c, v)) {
          g[r][c] = v;
          if (fill(pos + 1)) return true;
          g[r][c] = 0;
        }
      }
      return false;
    }
    fill(0);
    return g;
  }

  function newGame() {
    const full = generateFull();
    puzzle = full.map((row) => row.slice());
    // Zellen entfernen (symmetrisch fühlt sich hübscher an)
    const cells = [...Array(81).keys()].sort(() => Math.random() - 0.5);
    let removed = 0;
    for (const idx of cells) {
      if (removed >= difficulty) break;
      const r = Math.floor(idx / 9), c = idx % 9;
      if (puzzle[r][c] !== 0) { puzzle[r][c] = 0; removed++; }
    }
    board = puzzle.map((row) => row.slice());
    fixed = puzzle.map((row) => row.map((v) => v !== 0));
    selected = null;
    draw();
  }

  function conflicts(r, c) {
    const v = board[r][c];
    if (!v) return false;
    for (let i = 0; i < 9; i++) {
      if (i !== c && board[r][i] === v) return true;
      if (i !== r && board[i][c] === v) return true;
    }
    const br = r - r % 3, bc = c - c % 3;
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) {
      const rr = br + i, cc = bc + j;
      if ((rr !== r || cc !== c) && board[rr][cc] === v) return true;
    }
    return false;
  }

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;align-items:center;background:#0a1420;overflow:auto;padding:12px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;font-size:13px;flex-wrap:wrap;justify-content:center">
        <select id="su-diff" style="font-size:12px">
          <option value="35">Leicht</option>
          <option value="45" selected>Mittel</option>
          <option value="54">Schwer</option>
        </select>
        <button class="btn-primary" id="su-new" style="margin:0;width:auto;font-size:12px">🎲 Neues Spiel</button>
        <button class="taskbar-btn" id="su-check" style="font-size:12px">✔ Prüfen</button>
        <span id="su-msg" style="font-weight:700"></span>
      </div>
      <div id="su-grid"></div>
      <div id="su-pad" style="display:flex;gap:5px;margin-top:12px;flex-wrap:wrap;justify-content:center">
        ${[1,2,3,4,5,6,7,8,9].map((n) =>
          `<button data-n="${n}" class="taskbar-btn" style="width:36px;padding:8px 0;font-weight:700">${n}</button>`).join("")}
        <button data-n="0" class="taskbar-btn" style="padding:8px 12px">⌫</button>
      </div>
    </div>
  `;
  const gridEl = body.querySelector("#su-grid");
  const msgEl = body.querySelector("#su-msg");

  function draw() {
    let html = `<div style="display:grid;grid-template-columns:repeat(9,38px);background:#4da6ff;gap:1px;padding:2px;border-radius:6px">`;
    for (let r = 0; r < 9; r++) for (let c = 0; c < 9; c++) {
      const v = board[r][c];
      const isSel = selected && selected.r === r && selected.c === c;
      const sameV = selected && v && board[selected.r][selected.c] === v;
      const conf = !fixed[r][c] && conflicts(r, c);
      const borders = `${c % 3 === 0 && c ? "border-left:2px solid #4da6ff;" : ""}${r % 3 === 0 && r ? "border-top:2px solid #4da6ff;" : ""}`;
      html += `<div data-r="${r}" data-c="${c}" style="width:38px;height:38px;display:flex;align-items:center;justify-content:center;
        cursor:pointer;font-weight:${fixed[r][c] ? 800 : 500};font-size:17px;${borders}
        background:${isSel ? "#2a4a75" : sameV ? "#1d3a5c" : "#132132"};
        color:${conf ? "#ff4d6d" : fixed[r][c] ? "#e8eefc" : "#3ecf8e"}">${v || ""}</div>`;
    }
    html += `</div>`;
    gridEl.innerHTML = html;
    gridEl.querySelectorAll("[data-r]").forEach((el) =>
      el.addEventListener("click", () => {
        selected = { r: +el.dataset.r, c: +el.dataset.c };
        draw();
      }));
  }

  function setNum(n) {
    if (!selected || fixed[selected.r][selected.c]) return;
    board[selected.r][selected.c] = n;
    msgEl.textContent = "";
    draw();
  }
  body.querySelector("#su-pad").addEventListener("click", (e) => {
    const b = e.target.closest("[data-n]");
    if (b) setNum(+b.dataset.n);
  });
  body.tabIndex = 0;
  body.addEventListener("keydown", (e) => {
    if (/^[1-9]$/.test(e.key)) setNum(+e.key);
    else if (e.key === "Backspace" || e.key === "Delete" || e.key === "0") setNum(0);
    else if (selected && e.key.startsWith("Arrow")) {
      e.preventDefault();
      if (e.key === "ArrowUp") selected.r = Math.max(0, selected.r - 1);
      if (e.key === "ArrowDown") selected.r = Math.min(8, selected.r + 1);
      if (e.key === "ArrowLeft") selected.c = Math.max(0, selected.c - 1);
      if (e.key === "ArrowRight") selected.c = Math.min(8, selected.c + 1);
      draw();
    }
  });

  body.querySelector("#su-check").addEventListener("click", () => {
    let full = true, bad = false;
    for (let r = 0; r < 9; r++) for (let c = 0; c < 9; c++) {
      if (!board[r][c]) full = false;
      else if (conflicts(r, c)) bad = true;
    }
    msgEl.style.color = bad ? "#ff4d6d" : full ? "#3ecf8e" : "#ffd166";
    msgEl.textContent = bad ? "Konflikte gefunden!" : full ? "🎉 Gelöst!" : "Bisher fehlerfrei";
    if (full && !bad) {
      // Punkte nach Schwierigkeit (entfernte Zellen: 35/45/54)
      reportScore("sudoku", { 35: 100, 45: 200, 54: 300 }[difficulty] || 100);
    }
  });
  body.querySelector("#su-new").addEventListener("click", newGame);
  body.querySelector("#su-diff").addEventListener("change", (e) => {
    difficulty = +e.target.value; newGame();
  });
  difficulty = 45;
  newGame();
  setTimeout(() => body.focus(), 60);
}

// ================= SNAKE =================
export function renderSnake(body) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:#0a1420">
      <div style="display:flex;gap:12px;align-items:center;padding:8px 12px;background:var(--panel-2);font-size:13px">
        <span>🐍 Punkte: <b id="sn-score">0</b></span>
        <span>🏆 Rekord: <b id="sn-best">0</b></span>
        <span style="flex:1"></span>
        <span style="color:var(--subtext)">Pfeiltasten / WASD</span>
      </div>
      <div style="flex:1;position:relative;overflow:hidden">
        <canvas id="sn-canvas" style="display:block;width:100%;height:100%"></canvas>
        <div id="sn-overlay" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;background:rgba(10,20,32,0.85)">
          <h2 style="margin:0 0 10px">🐍 Snake</h2>
          <button class="btn-primary" id="sn-start" style="margin:0;width:auto">Start</button>
        </div>
      </div>
    </div>
  `;
  const canvas = body.querySelector("#sn-canvas");
  const ctx = canvas.getContext("2d");
  const scoreEl = body.querySelector("#sn-score");
  const bestEl = body.querySelector("#sn-best");
  const overlay = body.querySelector("#sn-overlay");
  const COLS = 26, ROWS = 18;
  let cell = 20, offX = 0, offY = 0;
  let snake, dir, nextDir, food, score = 0, best = 0, timer = null, speed = 140;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(300, rect.width);
    canvas.height = Math.max(200, rect.height);
    cell = Math.floor(Math.min(canvas.width / COLS, canvas.height / ROWS));
    offX = Math.floor((canvas.width - cell * COLS) / 2);
    offY = Math.floor((canvas.height - cell * ROWS) / 2);
  }

  function placeFood() {
    do {
      food = { x: Math.floor(Math.random() * COLS), y: Math.floor(Math.random() * ROWS) };
    } while (snake.some((s) => s.x === food.x && s.y === food.y));
  }

  function start() {
    snake = [{ x: 5, y: 9 }, { x: 4, y: 9 }, { x: 3, y: 9 }];
    dir = { x: 1, y: 0 }; nextDir = dir;
    score = 0; speed = 140;
    scoreEl.textContent = 0;
    placeFood();
    overlay.style.display = "none";
    clearInterval(timer);
    timer = setInterval(tick, speed);
    body.focus();
  }

  function tick() {
    dir = nextDir;
    const head = { x: (snake[0].x + dir.x + COLS) % COLS, y: (snake[0].y + dir.y + ROWS) % ROWS };
    if (snake.some((s) => s.x === head.x && s.y === head.y)) return gameOver();
    snake.unshift(head);
    if (head.x === food.x && head.y === food.y) {
      score += 10; scoreEl.textContent = score;
      if (score % 50 === 0 && speed > 60) { speed -= 10; clearInterval(timer); timer = setInterval(tick, speed); }
      placeFood();
    } else snake.pop();
    draw();
  }

  function draw() {
    ctx.fillStyle = "#0a1420";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#14243a";
    ctx.strokeRect(offX, offY, COLS * cell, ROWS * cell);
    ctx.font = `${cell - 2}px system-ui`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("🍎", offX + food.x * cell + cell / 2, offY + food.y * cell + cell / 2);
    snake.forEach((s, i) => {
      ctx.fillStyle = i === 0 ? "#3ecf8e" : `rgba(62,207,142,${Math.max(0.35, 1 - i * 0.04)})`;
      ctx.fillRect(offX + s.x * cell + 1, offY + s.y * cell + 1, cell - 2, cell - 2);
    });
  }

  function gameOver() {
    clearInterval(timer); timer = null;
    reportScore("snake", score);
    best = Math.max(best, score);
    bestEl.textContent = best;
    overlay.style.display = "flex";
    overlay.innerHTML = `
      <h2 style="margin:0 0 6px">💀 Game Over</h2>
      <p style="color:var(--subtext);font-size:13px;margin:0 0 10px">Punkte: <b>${score}</b> · Rekord: <b>${best}</b></p>
      <button class="btn-primary" id="sn-start" style="margin:0;width:auto">Nochmal</button>`;
    overlay.querySelector("#sn-start").addEventListener("click", start);
  }

  body.tabIndex = 0;
  body.addEventListener("keydown", (e) => {
    const map = { ArrowUp: [0,-1], w: [0,-1], ArrowDown: [0,1], s: [0,1],
                  ArrowLeft: [-1,0], a: [-1,0], ArrowRight: [1,0], d: [1,0] };
    const m = map[e.key];
    if (!m) return;
    e.preventDefault();
    if (m[0] === -dir.x && m[1] === -dir.y) return;   // kein 180°-Wenden
    nextDir = { x: m[0], y: m[1] };
  });
  overlay.querySelector("#sn-start").addEventListener("click", start);
  setTimeout(resize, 50);
}

// ================= MINESWEEPER =================
export function renderMinesweeper(body) {
  let cols = 12, rows = 10, mines = 16;
  let grid, revealed, flagged, over, firstClick;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;align-items:center;background:#0a1420;overflow:auto;padding:12px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;font-size:13px;flex-wrap:wrap;justify-content:center">
        <select id="ms-size" style="font-size:12px">
          <option value="9,9,10">Klein (9×9, 10 💣)</option>
          <option value="12,10,16" selected>Mittel (12×10, 16 💣)</option>
          <option value="18,12,36">Groß (18×12, 36 💣)</option>
        </select>
        <button class="btn-primary" id="ms-new" style="margin:0;width:auto;font-size:12px">🎲 Neu</button>
        <span>🚩 <b id="ms-flags">0</b>/<b id="ms-mines">0</b></span>
        <span id="ms-msg" style="font-weight:700"></span>
      </div>
      <div id="ms-grid"></div>
      <div style="color:var(--subtext);font-size:11px;margin-top:8px">Linksklick: aufdecken · Rechtsklick: Flagge</div>
    </div>
  `;
  const gridEl = body.querySelector("#ms-grid");
  const msgEl = body.querySelector("#ms-msg");
  const flagsEl = body.querySelector("#ms-flags");
  const minesEl = body.querySelector("#ms-mines");
  const NUM_COLORS = ["", "#4da6ff", "#3ecf8e", "#ff4d6d", "#c77dff", "#ffd166", "#8ecae6", "#fff", "#999"];

  function newGame() {
    grid = Array.from({ length: rows }, () => Array(cols).fill(0));
    revealed = Array.from({ length: rows }, () => Array(cols).fill(false));
    flagged = Array.from({ length: rows }, () => Array(cols).fill(false));
    over = false; firstClick = true;
    msgEl.textContent = "";
    minesEl.textContent = mines;
    flagsEl.textContent = 0;
    draw();
  }

  function placeMines(avoidR, avoidC) {
    let placed = 0;
    while (placed < mines) {
      const r = Math.floor(Math.random() * rows), c = Math.floor(Math.random() * cols);
      if (grid[r][c] === -1 || (Math.abs(r - avoidR) <= 1 && Math.abs(c - avoidC) <= 1)) continue;
      grid[r][c] = -1; placed++;
    }
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      if (grid[r][c] === -1) continue;
      let n = 0;
      for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
        const rr = r + dr, cc = c + dc;
        if (rr >= 0 && cc >= 0 && rr < rows && cc < cols && grid[rr][cc] === -1) n++;
      }
      grid[r][c] = n;
    }
  }

  function reveal(r, c) {
    if (r < 0 || c < 0 || r >= rows || c >= cols || revealed[r][c] || flagged[r][c]) return;
    revealed[r][c] = true;
    if (grid[r][c] === 0) {
      for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) reveal(r + dr, c + dc);
    }
  }

  function click(r, c, rightClick) {
    if (over) return;
    if (rightClick) {
      if (!revealed[r][c]) {
        flagged[r][c] = !flagged[r][c];
        flagsEl.textContent = flagged.flat().filter(Boolean).length;
      }
      draw(); return;
    }
    if (flagged[r][c]) return;
    if (firstClick) { placeMines(r, c); firstClick = false; }
    if (grid[r][c] === -1) {
      over = true;
      revealed = revealed.map((row) => row.map(() => true));
      msgEl.style.color = "#ff4d6d";
      msgEl.textContent = "💥 BOOM!";
      draw(); return;
    }
    reveal(r, c);
    const left = revealed.flat().filter((v) => !v).length;
    if (left === mines) {
      over = true;
      msgEl.style.color = "#3ecf8e";
      msgEl.textContent = "🎉 Gewonnen!";
      reportScore("minesweeper", mines * 10);   // größeres Feld = mehr Punkte
    }
    draw();
  }

  function draw() {
    let html = `<div style="display:grid;grid-template-columns:repeat(${cols},30px);gap:2px" oncontextmenu="return false">`;
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const rev = revealed[r][c];
      const v = grid[r][c];
      let content = "";
      if (rev) content = v === -1 ? "💣" : (v > 0 ? v : "");
      else if (flagged[r][c]) content = "🚩";
      html += `<div data-r="${r}" data-c="${c}" style="width:30px;height:30px;display:flex;align-items:center;justify-content:center;
        border-radius:4px;cursor:pointer;font-weight:800;font-size:14px;user-select:none;
        background:${rev ? "#132132" : "#2a4a75"};color:${NUM_COLORS[Math.max(0, v)] || "#fff"}">${content}</div>`;
    }
    html += `</div>`;
    gridEl.innerHTML = html;
    gridEl.querySelectorAll("[data-r]").forEach((el) => {
      el.addEventListener("click", () => click(+el.dataset.r, +el.dataset.c, false));
      el.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        click(+el.dataset.r, +el.dataset.c, true);
      });
    });
  }

  body.querySelector("#ms-new").addEventListener("click", newGame);
  body.querySelector("#ms-size").addEventListener("change", (e) => {
    [cols, rows, mines] = e.target.value.split(",").map(Number);
    newGame();
  });
  newGame();
}

// ================= TETRIS =================
export function renderTetris(body) {
  const COLS = 10, ROWS = 20;
  const SHAPES = {
    I: { color: "#4da6ff", cells: [[0,1],[1,1],[2,1],[3,1]] },
    O: { color: "#ffd166", cells: [[1,0],[2,0],[1,1],[2,1]] },
    T: { color: "#c77dff", cells: [[1,0],[0,1],[1,1],[2,1]] },
    S: { color: "#3ecf8e", cells: [[1,0],[2,0],[0,1],[1,1]] },
    Z: { color: "#ff4d6d", cells: [[0,0],[1,0],[1,1],[2,1]] },
    J: { color: "#8ecae6", cells: [[0,0],[0,1],[1,1],[2,1]] },
    L: { color: "#ffb703", cells: [[2,0],[0,1],[1,1],[2,1]] },
  };
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;background:#0a1420">
      <div style="display:flex;gap:12px;align-items:center;padding:8px 12px;background:var(--panel-2);font-size:13px;flex-wrap:wrap">
        <span>🧱 Punkte: <b id="tt-score">0</b></span>
        <span>Reihen: <b id="tt-lines">0</b></span>
        <span>Level: <b id="tt-level">1</b></span>
        <span style="flex:1"></span>
        <span style="color:var(--subtext)">←→ bewegen · ↑ drehen · ↓ schnell · Leertaste: Drop</span>
      </div>
      <div style="flex:1;position:relative;overflow:hidden">
        <canvas id="tt-canvas" style="display:block;width:100%;height:100%"></canvas>
        <div id="tt-overlay" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;background:rgba(10,20,32,0.85)">
          <h2 style="margin:0 0 10px">🧱 Tetris</h2>
          <button class="btn-primary" id="tt-start" style="margin:0;width:auto">Start</button>
        </div>
      </div>
    </div>
  `;
  const canvas = body.querySelector("#tt-canvas");
  const ctx = canvas.getContext("2d");
  const overlay = body.querySelector("#tt-overlay");
  const scoreEl = body.querySelector("#tt-score");
  const linesEl = body.querySelector("#tt-lines");
  const levelEl = body.querySelector("#tt-level");
  let cell = 22, offX = 0, offY = 0;
  let board, piece, score, lines, level, timer = null;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    canvas.width = Math.max(240, rect.width);
    canvas.height = Math.max(300, rect.height);
    cell = Math.floor(Math.min(canvas.width / (COLS + 2), canvas.height / (ROWS + 1)));
    offX = Math.floor((canvas.width - cell * COLS) / 2);
    offY = Math.floor((canvas.height - cell * ROWS) / 2);
  }

  function newPiece() {
    const keys = Object.keys(SHAPES);
    const k = keys[Math.floor(Math.random() * keys.length)];
    return { cells: SHAPES[k].cells.map((c) => c.slice()), color: SHAPES[k].color, x: 3, y: 0, type: k };
  }

  const collides = (cells, px, py) =>
    cells.some(([cx, cy]) => {
      const x = px + cx, y = py + cy;
      return x < 0 || x >= COLS || y >= ROWS || (y >= 0 && board[y][x]);
    });

  function rotate() {
    if (piece.type === "O") return;
    const rotated = piece.cells.map(([x, y]) => [1 - (y - 1), 1 + (x - 1)]);  // um (1,1)
    for (const kick of [0, -1, 1, -2, 2]) {
      if (!collides(rotated, piece.x + kick, piece.y)) {
        piece.cells = rotated; piece.x += kick; return;
      }
    }
  }

  function lockPiece() {
    for (const [cx, cy] of piece.cells) {
      const y = piece.y + cy;
      if (y < 0) return gameOver();
      board[y][piece.x + cx] = piece.color;
    }
    let cleared = 0;
    board = board.filter((row) => {
      if (row.every(Boolean)) { cleared++; return false; }
      return true;
    });
    while (board.length < ROWS) board.unshift(Array(COLS).fill(null));
    if (cleared) {
      lines += cleared;
      score += [0, 100, 300, 500, 800][cleared] * level;
      const newLevel = 1 + Math.floor(lines / 10);
      if (newLevel !== level) { level = newLevel; restartTimer(); }
      scoreEl.textContent = score; linesEl.textContent = lines; levelEl.textContent = level;
    }
    piece = newPiece();
    if (collides(piece.cells, piece.x, piece.y)) gameOver();
  }

  function tick() {
    if (!collides(piece.cells, piece.x, piece.y + 1)) piece.y++;
    else lockPiece();
    draw();
  }
  function restartTimer() {
    clearInterval(timer);
    timer = setInterval(tick, Math.max(80, 550 - (level - 1) * 45));
  }

  function draw() {
    ctx.fillStyle = "#0a1420";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#33507088";
    ctx.strokeRect(offX - 1, offY - 1, COLS * cell + 2, ROWS * cell + 2);
    const cellRect = (x, y, color) => {
      ctx.fillStyle = color;
      ctx.fillRect(offX + x * cell + 1, offY + y * cell + 1, cell - 2, cell - 2);
    };
    for (let y = 0; y < ROWS; y++) for (let x = 0; x < COLS; x++)
      if (board[y][x]) cellRect(x, y, board[y][x]);
    if (piece) for (const [cx, cy] of piece.cells)
      if (piece.y + cy >= 0) cellRect(piece.x + cx, piece.y + cy, piece.color);
  }

  function start() {
    board = Array.from({ length: ROWS }, () => Array(COLS).fill(null));
    piece = newPiece();
    score = 0; lines = 0; level = 1;
    scoreEl.textContent = 0; linesEl.textContent = 0; levelEl.textContent = 1;
    overlay.style.display = "none";
    resize();
    restartTimer();
    draw();
    body.focus();
  }

  function gameOver() {
    clearInterval(timer); timer = null;
    reportScore("tetris", score);
    overlay.style.display = "flex";
    overlay.innerHTML = `
      <h2 style="margin:0 0 6px">💀 Game Over</h2>
      <p style="color:var(--subtext);font-size:13px;margin:0 0 10px">Punkte: <b>${score}</b> · Reihen: <b>${lines}</b></p>
      <button class="btn-primary" id="tt-start" style="margin:0;width:auto">Nochmal</button>`;
    overlay.querySelector("#tt-start").addEventListener("click", start);
  }

  body.tabIndex = 0;
  body.addEventListener("keydown", (e) => {
    if (!piece || !timer) return;
    if (e.key === "ArrowLeft" && !collides(piece.cells, piece.x - 1, piece.y)) piece.x--;
    else if (e.key === "ArrowRight" && !collides(piece.cells, piece.x + 1, piece.y)) piece.x++;
    else if (e.key === "ArrowDown") tick();
    else if (e.key === "ArrowUp") rotate();
    else if (e.key === " ") { while (!collides(piece.cells, piece.x, piece.y + 1)) piece.y++; tick(); }
    else return;
    e.preventDefault();
    draw();
  });
  overlay.querySelector("#tt-start").addEventListener("click", start);
  setTimeout(resize, 50);
}
