// apps/speedtest.js
// -----------------
// Speedtest im Ookla-Stil: grosse Halbkreis-Anzeige (logarithmische Skala
// 0-1000 Mbit/s), Phasen Ping -> Download -> Upload, Live-Nadel, danach
// Ergebnis-Kacheln. Gemessen wird Browser <-> RMM-Server.
//
// Technik: mehrere parallele Streams (wie Ookla), Download via Streaming-
// Reader (Bytes live gezaehlt), Upload via wiederholten POSTs mit
// unkomprimierbaren Zufallsdaten. Endpunkte: backend/routers/speedtest_routes.py

import { registerCleanup } from "../windowmanager.js";

const DURATION_MS = 8000;      // Messdauer je Richtung
const DL_STREAMS = 4;
const UL_STREAMS = 3;
const UL_CHUNK = 2 * 1024 * 1024;   // 2 MiB pro Upload-Request

function authHeaders() {
  const t = localStorage.getItem("rmm_token");
  return t ? { Authorization: `Bearer ${t}` } : {};
}

// Log-Skala wie Ookla: 0..1000 Mbit/s auf 0..1
const scalePos = (mbps) => Math.min(1, Math.log10(1 + Math.max(0, mbps)) / 3); // log10(1001)~3
const fmt = (v, d = 1) => (v >= 100 ? Math.round(v) : v.toFixed(d));

export function renderSpeedtest(body, win) {
  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%;align-items:center;overflow:auto;padding:14px 10px;gap:10px">

      <!-- Halbkreis-Tacho -->
      <div style="position:relative;width:min(340px,90%)">
        <svg viewBox="0 0 200 118" style="width:100%;display:block">
          <path id="st-arc-bg" d="" fill="none" stroke="var(--border)" stroke-width="10" stroke-linecap="round"/>
          <path id="st-arc-fg" d="" fill="none" stroke="var(--accent)" stroke-width="10" stroke-linecap="round"/>
          <g id="st-ticks" font-size="7" fill="var(--subtext)" text-anchor="middle"></g>
          <line id="st-needle" x1="100" y1="100" x2="100" y2="30" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round"/>
          <circle cx="100" cy="100" r="4.5" fill="var(--accent)"/>
        </svg>
        <div style="position:absolute;left:0;right:0;top:56%;text-align:center">
          <div id="st-value" style="font-size:34px;font-weight:800;line-height:1">–</div>
          <div id="st-unit" style="color:var(--subtext);font-size:12px">Mbit/s</div>
        </div>
      </div>

      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:center">
        <label style="color:var(--subtext);font-size:12px">Testziel:</label>
        <select id="st-mode" style="min-height:32px">
          <option value="ndt7">Google / M-Lab (Browser-Internet, wie speed.measurementlab.net)</option>
          <option value="local">Browser ↔ RMM-Server (LAN/WAN-Strecke)</option>
        </select>
      </div>

      <div id="st-phase" style="color:var(--subtext);font-size:13px;min-height:18px"></div>
      <div id="st-server" style="color:var(--subtext);font-size:11.5px;min-height:15px;text-align:center"></div>

      <button id="st-start" class="btn-primary"
              style="min-width:150px;min-height:46px;font-size:16px;border-radius:23px">Start</button>

      <!-- Ergebnis-Kacheln -->
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px;width:100%;max-width:520px">
        ${[["ping", "Ping", "ms"], ["jitter", "Jitter", "ms"],
           ["down", "⬇️ Download", "Mbit/s"], ["up", "⬆️ Upload", "Mbit/s"]].map(([id, label, unit]) => `
          <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:10px;padding:10px;text-align:center">
            <div style="color:var(--subtext);font-size:11.5px">${label}</div>
            <div id="st-r-${id}" style="font-size:20px;font-weight:700;margin-top:2px">–</div>
            <div style="color:var(--subtext);font-size:10.5px">${unit}</div>
          </div>`).join("")}
      </div>

      <div id="st-note" style="color:var(--subtext);font-size:11.5px;text-align:center;max-width:520px"></div>
    </div>`;

  const $ = (id) => body.querySelector(`#${id}`);
  const needle = $("st-needle"), valueEl = $("st-value"), phaseEl = $("st-phase");
  const startBtn = $("st-start");

  // ---- Tacho-Geometrie: Halbkreis von 210° bis -30° (240° Bogen) ----
  const CX = 100, CY = 100, R = 78;
  const A0 = 210, A1 = -30;
  const pt = (deg, r = R) => {
    const rad = (deg * Math.PI) / 180;
    return [CX + r * Math.cos(rad), CY - r * Math.sin(rad)];
  };
  const arcPath = (fromDeg, toDeg) => {
    const [x1, y1] = pt(fromDeg), [x2, y2] = pt(toDeg);
    const large = fromDeg - toDeg > 180 ? 1 : 0;
    return `M ${x1} ${y1} A ${R} ${R} 0 ${large} 1 ${x2} ${y2}`;
  };
  $("st-arc-bg").setAttribute("d", arcPath(A0, A1));
  const fgArc = $("st-arc-fg");

  // Skalen-Beschriftung (log): 0,1,5,10,50,100,250,500,1000
  const ticks = $("st-ticks");
  [0, 1, 5, 10, 50, 100, 250, 500, 1000].forEach((v) => {
    const deg = A0 - (A0 - A1) * scalePos(v);
    const [x, y] = pt(deg, R - 15);
    const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
    t.setAttribute("x", x); t.setAttribute("y", y + 2.5); t.textContent = v;
    ticks.appendChild(t);
  });

  let displayed = 0;   // gedaempfte Nadel
  let gaugeTarget = 0; // Zielwert, den die Nadel anstrebt
  function setGauge(mbps) {
    gaugeTarget = Math.max(0, mbps || 0);
  }
  // Eigene, gleichmaessige Animationsschleife (unabhaengig von der Poll-Rate):
  // die Nadel laeuft mit ~60 fps sanft auf gaugeTarget zu - kein Ruckeln mehr,
  // egal wie schnell/langsam Statusdaten reinkommen.
  let gaugeRAF = 0;
  function gaugeTick() {
    displayed += (gaugeTarget - displayed) * 0.18;
    if (Math.abs(gaugeTarget - displayed) < 0.05) displayed = gaugeTarget;
    const pos = scalePos(displayed);
    const deg = A0 - (A0 - A1) * pos;
    const [x2, y2] = pt(deg, R - 12);
    needle.setAttribute("x2", x2); needle.setAttribute("y2", y2);
    fgArc.setAttribute("d", pos > 0.002 ? arcPath(A0, deg) : "");
    valueEl.textContent = fmt(displayed);
    gaugeRAF = requestAnimationFrame(gaugeTick);
  }
  setGauge(0); displayed = 0; gaugeTick();  // Nadel-Animation dauerhaft laufen lassen

  // ---- Messphasen ----
  let running = false;
  let closed = false;               // Fenster geschlossen -> alles abbrechen
  let ndtSockets = [];              // offene NDT7-WebSockets (fuer Abbruch)
  const aborters = [];
  const cleanupAbort = () => { aborters.splice(0).forEach((a) => { try { a.abort(); } catch {} }); };

  // Beim Schliessen des Fensters: laufende Messung hart stoppen (lokale Fetches
  // abbrechen, Nadel-Animation beenden, offene NDT7-WebSockets schliessen).
  registerCleanup(win.key, () => {
    closed = true;
    cleanupAbort();
    if (gaugeRAF) cancelAnimationFrame(gaugeRAF);
    (ndtSockets || []).forEach((s) => { try { s.close(); } catch {} });
  });

  async function measurePing() {
    phaseEl.textContent = "Ping wird gemessen…";
    const times = [];
    for (let i = 0; i < 6; i++) {
      const t0 = performance.now();
      await fetch(`/api/speedtest/ping?nc=${Date.now()}${i}`, { cache: "no-store", headers: authHeaders() });
      times.push(performance.now() - t0);
    }
    times.shift();                                    // Aufwaermer verwerfen
    const ping = Math.min(...times);
    let jitter = 0;
    for (let i = 1; i < times.length; i++) jitter += Math.abs(times[i] - times[i - 1]);
    jitter /= times.length - 1;
    $("st-r-ping").textContent = fmt(ping);
    $("st-r-jitter").textContent = fmt(jitter);
    return { ping, jitter };
  }

  // Gemeinsamer Live-Ticker: Bytes-Zaehler -> Mbit/s auf den Tacho
  function liveMeter(getBytes, t0) {
    const iv = setInterval(() => {
      const secs = (performance.now() - t0) / 1000;
      if (secs > 0.3) setGauge((getBytes() * 8) / secs / 1e6);
    }, 120);
    return () => clearInterval(iv);
  }

  async function measureDownload() {
    phaseEl.textContent = "Download wird gemessen…";
    displayed = 0;
    let bytes = 0;
    const t0 = performance.now();
    const deadline = t0 + DURATION_MS;
    const stopMeter = liveMeter(() => bytes, t0);

    const worker = async () => {
      while (performance.now() < deadline && !closed) {
        const ac = new AbortController(); aborters.push(ac);
        // Deadline hart durchsetzen, auch mitten im Stream
        const kill = setTimeout(() => ac.abort(), Math.max(50, deadline - performance.now()));
        try {
          const res = await fetch(`/api/speedtest/download?mb=32&nc=${Date.now()}${Math.random()}`,
            { cache: "no-store", signal: ac.signal, headers: authHeaders() });
          const reader = res.body.getReader();
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            bytes += value.length;
          }
        } catch { /* Abort am Ende ist Absicht */ }
        clearTimeout(kill);
      }
    };
    await Promise.all(Array.from({ length: DL_STREAMS }, worker));
    stopMeter();
    const mbps = (bytes * 8) / ((performance.now() - t0) / 1000) / 1e6;
    $("st-r-down").textContent = fmt(mbps);
    return mbps;
  }

  async function measureUpload() {
    phaseEl.textContent = "Upload wird gemessen…";
    displayed = 0;
    // Unkomprimierbare Zufallsdaten (crypto liefert max 64 KiB pro Aufruf)
    const chunk = new Uint8Array(UL_CHUNK);
    for (let off = 0; off < UL_CHUNK; off += 65536) {
      crypto.getRandomValues(chunk.subarray(off, Math.min(off + 65536, UL_CHUNK)));
    }
    const blob = new Blob([chunk]);

    let bytes = 0;
    const t0 = performance.now();
    const deadline = t0 + DURATION_MS;
    const stopMeter = liveMeter(() => bytes, t0);

    const worker = async () => {
      while (performance.now() < deadline && !closed) {
        const ac = new AbortController(); aborters.push(ac);
        const kill = setTimeout(() => ac.abort(), Math.max(50, deadline - performance.now()));
        try {
          await fetch(`/api/speedtest/upload?nc=${Date.now()}${Math.random()}`, {
            method: "POST", body: blob, cache: "no-store",
            signal: ac.signal, headers: authHeaders(),
          });
          bytes += blob.size;               // Request komplett angekommen
        } catch { /* Abort = Zeit um */ }
        clearTimeout(kill);
      }
    };
    await Promise.all(Array.from({ length: UL_STREAMS }, worker));
    stopMeter();
    const mbps = (bytes * 8) / ((performance.now() - t0) / 1000) / 1e6;
    $("st-r-up").textContent = fmt(mbps);
    return mbps;
  }

  // ---- NDT7-Modus (M-Lab / dieselbe Engine wie Googles Speedtest &
  // speed.measurementlab.net): laeuft KOMPLETT im Browser ueber WebSockets zu
  // oeffentlichen M-Lab-Servern. Kein Server-Binary, kein Backend, kein CORS-
  // Problem. Gemessen wird die echte Internet-Anbindung DIESES Geraets.
  const PHASE_TEXT = {
    server: "M-Lab-Server wird gewählt…",
    ping: "Latenz wird gemessen…",
    download: "Download wird gemessen…",
    upload: "Upload wird gemessen…",
    done: "Fertig ✓",
  };

  // Naechstgelegene M-Lab-Server holen (liefert wss-URLs fuer download/upload)
  async function locateServers() {
    const url = "https://locate.measurementlab.net/v2/nearest/ndt/ndt7"
      + "?client_name=rapalle-rmm&client_version=1.0";
    let r;
    try {
      r = await fetch(url, { cache: "no-store" });
    } catch (e) {
      throw new Error("M-Lab-Serverauswahl nicht erreichbar (Internet/Firewall?).");
    }
    if (!r.ok) throw new Error(`Serverauswahl fehlgeschlagen (HTTP ${r.status})`);
    const j = await r.json();
    if (!j.results || !j.results.length) throw new Error("Kein M-Lab-Server verfügbar");
    return j.results[0];   // { machine, location, urls: {...} }
  }

  // Eine NDT7-Richtung messen. dir = "download" | "upload".
  // onRate(mbps) wird laufend mit der aktuellen Rate aufgerufen.
  function measureNdt(wssUrl, dir, onRate) {
    return new Promise((resolve, reject) => {
      let ws;
      try {
        ws = new WebSocket(wssUrl, "net.measurementlab.ndt.v7");
      } catch (e) { reject(e); return; }
      ws.binaryType = "arraybuffer";
      ndtSockets.push(ws);

      // Sicherheitsnetz: oeffnet der Socket nicht binnen 8s (Firewall blockt
      // wss, kein Internet, ...), sauber mit Fehler abbrechen statt still zu
      // haengen (dann kaeme "kein Wert").
      let opened = false;
      const openTimer = setTimeout(() => {
        if (!opened) { try { ws.close(); } catch {}; reject(new Error(`${dir}: keine Verbindung zum M-Lab-Server (Firewall/Proxy blockt WebSockets?)`)); }
      }, 8000);

      let finalMbps = 0;
      const t0 = () => performance.now();
      let start = 0;
      let sentBytes = 0;
      let recvBytes = 0;        // im Download tatsaechlich empfangene Bytes
      let uploadTimer = null;
      const CHUNK = 1 << 16;                    // 64 KiB
      const payload = new Uint8Array(CHUNK);    // Uploaddaten (Nullen sind ok fuer NDT7)

      const cleanup = () => {
        clearTimeout(openTimer);
        if (uploadTimer) { clearInterval(uploadTimer); uploadTimer = null; }
        ndtSockets = ndtSockets.filter((s) => s !== ws);
      };

      ws.onopen = () => {
        opened = true; clearTimeout(openTimer);
        start = t0();
        if (dir === "upload") {
          // So viel senden, wie der Socket-Puffer aufnimmt (max ~1s Vorlauf)
          uploadTimer = setInterval(() => {
            if (closed) { try { ws.close(); } catch {}; return; }
            while (ws.bufferedAmount < 8 * CHUNK) {
              ws.send(payload);
              sentBytes += CHUNK;
            }
            const secs = (t0() - start) / 1000;
            if (secs > 0.2) {
              // Client-seitige Schaetzung (tatsaechlich gesendet minus Puffer)
              const acked = sentBytes - ws.bufferedAmount;
              const mbps = (acked * 8) / secs / 1e6;
              if (mbps > 0) { finalMbps = mbps; onRate(mbps); }
            }
          }, 100);
        }
      };

      ws.onmessage = (ev) => {
        if (closed) { try { ws.close(); } catch {}; return; }
        if (dir === "download") {
          // Download: Rate rein clientseitig aus tatsaechlich empfangenen Bytes.
          // Binaerframes koennen je nach Browser als ArrayBuffer ODER Blob
          // ankommen; Text = Server-Messungen (mitzaehlen ist vernachlaessigbar).
          const d = ev.data;
          if (typeof d === "string") recvBytes += d.length;
          else if (d && d.byteLength != null) recvBytes += d.byteLength;
          else if (d && d.size != null) recvBytes += d.size;
          const secs = (t0() - start) / 1000;
          if (secs > 0.15 && recvBytes > 0) {
            const mbps = (recvBytes * 8) / secs / 1e6;
            finalMbps = mbps; onRate(mbps);
          }
        } else if (typeof ev.data === "string") {
          // Upload: der Server meldet seine empfangene Datenmenge (autoritativ).
          try {
            const m = JSON.parse(ev.data);
            const tcp = m.TCPInfo || {};
            if (tcp.BytesReceived && tcp.ElapsedTime) {
              const v = (tcp.BytesReceived * 8) / tcp.ElapsedTime; // Mbit/s
              if (v > 0) { finalMbps = v; onRate(v); }
            }
          } catch { /* ignorieren */ }
        }
      };

      ws.onclose = () => { cleanup(); resolve(finalMbps); };
      ws.onerror = () => { cleanup(); reject(new Error(`${dir}-Verbindung fehlgeschlagen`)); };
    });
  }

  async function runNdt7() {
    phaseEl.textContent = PHASE_TEXT.server;
    const srv = await locateServers();
    const loc = srv.location || {};
    $("st-server").textContent =
      `Server: ${srv.machine || "M-Lab"}` +
      (loc.city ? ` · ${loc.city}` : "") + (loc.country ? `, ${loc.country}` : "");

    const urls = srv.urls || {};
    const dlUrl = urls["wss:///ndt/v7/download"] || urls["ws:///ndt/v7/download"];
    const ulUrl = urls["wss:///ndt/v7/upload"] || urls["ws:///ndt/v7/upload"];
    if (!dlUrl || !ulUrl) throw new Error("M-Lab-Server liefert keine gültigen URLs");

    // Download
    phaseEl.textContent = PHASE_TEXT.download; displayed = 0;
    const down = await measureNdt(dlUrl, "download", (mbps) => {
      setGauge(mbps);
      $("st-r-down").textContent = fmt(mbps);
    });
    if (closed) return;
    $("st-r-down").textContent = fmt(down);
    setGauge(down);

    // Upload
    phaseEl.textContent = PHASE_TEXT.upload; displayed = 0;
    const up = await measureNdt(ulUrl, "upload", (mbps) => {
      setGauge(mbps);
      $("st-r-up").textContent = fmt(mbps);
    });
    if (closed) return;
    $("st-r-up").textContent = fmt(up);
    setGauge(down);   // Nadel am Ende auf Download-Endwert

    // Ping/Jitter: NDT7 liefert Latenz via MinRTT nicht immer stabil -> wir
    // messen sie separat kurz gegen den RMM-Server (schnell & robust).
    try {
      const { ping, jitter } = await measurePing();
      $("st-r-ping").textContent = fmt(ping);
      $("st-r-jitter").textContent = fmt(jitter);
    } catch { /* Ping optional */ }
  }

  function applyModeUi() {
    const ndt = $("st-mode").value === "ndt7";
    $("st-note").textContent = ndt
      ? "Nutzt M-Lab NDT7 – dieselbe offene Messinfrastruktur wie Googles Speedtest (speed.measurementlab.net). Läuft direkt im Browser über WebSockets gegen öffentliche M-Lab-Server und misst die Internet-Anbindung dieses Geräts. Messungen sind öffentlich einsehbar (M-Lab-Datenschutz)."
      : `Misst die Verbindung zwischen diesem Browser und dem RMM-Server (${location.host}) über ${DL_STREAMS} parallele Streams.`;
    $("st-server").textContent = "";
    $("st-r-jitter").textContent = "–";
  }
  $("st-mode").addEventListener("change", applyModeUi);
  applyModeUi();

  startBtn.addEventListener("click", async () => {
    if (running) return;
    running = true;
    startBtn.disabled = true; startBtn.textContent = "Läuft…";
    ["ping", "jitter", "down", "up"].forEach((id) => ($(`st-r-${id}`).textContent = "–"));
    $("st-server").textContent = "";
    const isNdt = $("st-mode").value === "ndt7";
    try {
      if (isNdt) {
        await runNdt7();
        if (!closed) phaseEl.textContent = "Fertig ✓";
      } else {
        await measurePing();
        await measureDownload();
        await measureUpload();
        phaseEl.textContent = "Fertig ✓";
      }
    } catch (e) {
      phaseEl.textContent = `Messung fehlgeschlagen: ${e.message || "Verbindung prüfen."}`;
    } finally {
      cleanupAbort();
      // Nadel-Endwert nach erfolgreichem NDT7-Lauf stehen lassen, sonst 0.
      if (!isNdt || closed) { setGauge(0); displayed = 0; valueEl.textContent = "–"; }
      startBtn.disabled = false; startBtn.textContent = "Erneut testen";
      running = false;
    }
  });
}
