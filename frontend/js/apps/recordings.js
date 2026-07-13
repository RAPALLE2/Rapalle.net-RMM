// apps/recordings.js
// ------------------
// Zeigt aufgezeichnete Remote-Screen-Sessions an und spielt sie ab.
// Die Aufnahme besteht aus einzelnen JPEG-Frames mit Zeitstempeln (siehe
// backend/app/recording.py). Der Player zeigt sie wie ein Daumenkino mit den
// echten Zeitabständen ab und bietet Play/Pause + eine Zeitleiste zum Spulen.

import { api } from "../api.js";
import { registerCleanup } from "../windowmanager.js";
import { esc, uiConfirm } from "../utils.js";
import { MiniTerm } from "./miniterm.js";

function formatDuration(ms) {
  if (ms == null) return "–";
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

export function renderRecordings(body, win) {
  // Optional: eine bestimmte Aufzeichnung, die beim Öffnen direkt gezeigt wird
  // (z.B. wenn aus dem Audit-Log auf „Aufzeichnung ansehen" geklickt wurde).
  let preselectRecId = win.props?.recId || null;

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;height:100%">
      <div class="explorer-toolbar">
        <span style="flex:1;color:var(--subtext)">Aufgezeichnete Remote-Sessions (Auto-Löschung nach 10 Tagen)</span>
        <button id="rec-refresh-${win.key}">⟳</button>
      </div>
      <div style="flex:1;display:flex;min-height:0">
        <!-- Linke Spalte: Liste -->
        <div style="width:280px;border-right:1px solid var(--border);overflow:auto">
          <table class="data-table">
            <tbody id="rec-list-${win.key}"><tr><td style="color:var(--subtext)">Lädt...</td></tr></tbody>
          </table>
        </div>
        <!-- Rechte Spalte: Player -->
        <div style="flex:1;display:flex;flex-direction:column;background:#000;min-width:0">
          <div id="rec-player-${win.key}" style="flex:1;display:flex;align-items:center;justify-content:center;overflow:hidden">
            <span style="color:var(--subtext)">Wähle links eine Aufzeichnung.</span>
          </div>
          <div id="rec-controls-${win.key}" style="padding:8px;background:var(--panel-2);display:none;align-items:center;gap:10px">
            <button class="taskbar-btn" id="rec-play-${win.key}">▶</button>
            <input type="range" id="rec-seek-${win.key}" min="0" max="100" value="0" style="flex:1" />
            <span id="rec-time-${win.key}" style="font-size:11px;color:var(--subtext);font-family:monospace">0s</span>
          </div>
        </div>
      </div>
    </div>
  `;

  const listEl = body.querySelector(`#rec-list-${win.key}`);
  const playerEl = body.querySelector(`#rec-player-${win.key}`);
  const controlsEl = body.querySelector(`#rec-controls-${win.key}`);
  const playBtn = body.querySelector(`#rec-play-${win.key}`);
  const seekBar = body.querySelector(`#rec-seek-${win.key}`);
  const timeLabel = body.querySelector(`#rec-time-${win.key}`);

  // Player-Zustand
  let frames = [];
  let playing = false;
  let playTimer = null;
  let currentIdx = 0;
  let imgEl = null;
  let allRecordings = [];
  let videoUrl = null;   // Object-URL des aktuell geladenen Videos
  let termMode = false;  // aktuell ein Terminal-Replay (format 'term')?
  let replayTerm = null; // MiniTerm-Instanz für Terminal-Replays

  function stopPlayback() {
    playing = false;
    playBtn.textContent = "▶";
    if (playTimer) { clearTimeout(playTimer); playTimer = null; }
  }

  function disposeTerm() {
    try { replayTerm?.dispose(); } catch {}
    replayTerm = null;
    termMode = false;
  }

  // --- Terminal-Replay: Frames sind {t: ms, d: rohe Shell-Ausgabe}. Zum
  // Anzeigen bis Index N wird die Ausgabe 0..N durch den Terminal-Emulator
  // geschickt (Neuaufbau bei Rücksprung, sonst inkrementell). ---
  let termWrittenUpTo = -1;
  function showTermFrame(idx) {
    if (!frames.length || !replayTerm) return;
    currentIdx = Math.max(0, Math.min(frames.length - 1, idx));
    if (currentIdx < termWrittenUpTo) {
      // Rückwärts gespult -> Terminal neu aufbauen und bis idx erneut schreiben.
      const host = replayTerm.host;
      try { replayTerm.dispose(); } catch {}
      replayTerm = new MiniTerm(host, {});
      termWrittenUpTo = -1;
    }
    for (let i = termWrittenUpTo + 1; i <= currentIdx; i++) {
      replayTerm.write(frames[i].d || "");
    }
    termWrittenUpTo = currentIdx;
    seekBar.value = frames.length > 1 ? (currentIdx / (frames.length - 1)) * 100 : 100;
    timeLabel.textContent = Math.round(frames[currentIdx].t / 1000) + "s";
  }

  function releaseVideo() {
    if (videoUrl) { try { URL.revokeObjectURL(videoUrl); } catch {} videoUrl = null; }
  }

  function showFrame(idx) {
    if (termMode) { showTermFrame(idx); return; }
    if (!frames.length || !imgEl) return;
    currentIdx = Math.max(0, Math.min(frames.length - 1, idx));
    const frame = frames[currentIdx];
    imgEl.src = "data:image/jpeg;base64," + frame.img;
    seekBar.value = (currentIdx / (frames.length - 1)) * 100;
    timeLabel.textContent = Math.round(frame.t / 1000) + "s";
  }

  function scheduleNext() {
    if (!playing || currentIdx >= frames.length - 1) { stopPlayback(); return; }
    const cur = frames[currentIdx];
    const next = frames[currentIdx + 1];
    const delay = Math.max(30, Math.min(2000, next.t - cur.t)); // echte Zeitabstände
    playTimer = setTimeout(() => {
      showFrame(currentIdx + 1);
      scheduleNext();
    }, delay);
  }

  playBtn.addEventListener("click", () => {
    if (playing) { stopPlayback(); return; }
    if (currentIdx >= frames.length - 1) currentIdx = 0; // von vorne
    playing = true;
    playBtn.textContent = "⏸";
    showFrame(currentIdx);
    scheduleNext();
  });

  seekBar.addEventListener("input", () => {
    stopPlayback();
    const idx = Math.round((seekBar.value / 100) * (frames.length - 1));
    showFrame(idx);
  });

  async function loadRecording(recId) {
    stopPlayback();
    releaseVideo();
    disposeTerm();
    const rec = allRecordings.find((r) => r.id === recId);
    playerEl.innerHTML = `<span style="color:var(--subtext)">Lädt Aufzeichnung...</span>`;

    // Terminal-Replays (format 'term'): rohe Shell-Ausgabe mit Zeitstempeln,
    // Wiedergabe über den eingebauten Terminal-Emulator (MiniTerm).
    if (rec && rec.format === "term") {
      try {
        const res = await api.getRecordingFrames(recId);
        frames = res.frames || [];
        if (!frames.length) {
          playerEl.innerHTML = `<span style="color:var(--subtext)">Keine Daten in dieser Aufzeichnung.</span>`;
          controlsEl.style.display = "none";
          return;
        }
        playerEl.innerHTML = "";
        const host = document.createElement("div");
        host.style.cssText = "width:100%;height:100%;min-height:0";
        playerEl.appendChild(host);
        replayTerm = new MiniTerm(host, {});
        termMode = true;
        termWrittenUpTo = -1;
        controlsEl.style.display = "flex";
        currentIdx = 0;
        showFrame(0);
      } catch (e) {
        const gone = /nicht gefunden|fehlt|404/i.test(e.message || "");
        playerEl.innerHTML = `<div style="color:${gone ? "var(--danger)" : "var(--subtext)"};padding:20px;text-align:center">
          ${gone ? "Replay gibt's nicht mehr – die Datei wurde gelöscht." : esc(e.message)}</div>`;
        if (gone) loadList();   // Liste auffrischen (Eintrag wird serverseitig entfernt)
      }
      return;
    }

    // Neue 1:1-Aufzeichnungen sind Videos (WebM) und werden direkt abgespielt.
    if (rec && rec.format === "video") {
      controlsEl.style.display = "none";   // <video> bringt eigene Steuerung mit
      try {
        const blob = await api.getRecordingVideoBlob(recId);
        videoUrl = URL.createObjectURL(blob);
        playerEl.innerHTML = `<video controls autoplay style="max-width:100%;max-height:100%;background:#000" src="${videoUrl}"></video>`;
      } catch (e) {
        const gone = /nicht gefunden|fehlt|404/i.test(e.message || "");
        playerEl.innerHTML = `<div style="color:var(--danger);padding:20px;text-align:center">${gone ? "Replay gibt's nicht mehr – die Datei wurde gelöscht." : esc(e.message)}</div>`;
        if (gone) loadList();
      }
      return;
    }

    // Alte Frame-basierte Aufzeichnungen (Rückwärtskompatibilität).
    try {
      const res = await api.getRecordingFrames(recId);
      frames = res.frames || [];
      if (!frames.length) {
        playerEl.innerHTML = `<span style="color:var(--subtext)">Keine Frames in dieser Aufzeichnung.</span>`;
        controlsEl.style.display = "none";
        return;
      }
      playerEl.innerHTML = `<img id="rec-img-${win.key}" style="max-width:100%;max-height:100%;object-fit:contain" />`;
      imgEl = playerEl.querySelector(`#rec-img-${win.key}`);
      controlsEl.style.display = "flex";
      currentIdx = 0;
      showFrame(0);
    } catch (e) {
      const gone = /nicht gefunden|fehlt|404/i.test(e.message || "");
      playerEl.innerHTML = `<div style="color:var(--danger);padding:20px;text-align:center">${gone ? "Replay gibt's nicht mehr – die Datei wurde gelöscht." : esc(e.message)}</div>`;
      if (gone) loadList();
    }
  }

  async function loadList() {
    try {
      const recordings = await api.getRecordings();
      allRecordings = recordings;
      if (!recordings.length) {
        listEl.innerHTML = `<tr><td style="color:var(--subtext)">Noch keine Aufzeichnungen.</td></tr>`;
        return;
      }
      listEl.innerHTML = recordings.map((r) => {
        const missing = r.file_exists === false;
        return `
        <tr ${missing ? "" : `data-rec="${r.id}"`} style="cursor:${missing ? "default" : "pointer"};${missing ? "opacity:0.6" : ""}">
          <td>
            <div style="font-weight:500">${esc(r.client_hostname || "?")}</div>
            <div style="font-size:11px;color:var(--subtext)">
              ${new Date(r.started_at).toLocaleString("de-DE")}<br>
              ${missing
                ? `<span style="color:var(--danger)">Replay gibt's nicht mehr (Datei wurde gelöscht)</span>`
                : `${formatDuration(r.duration_ms)} · ${r.format === "video" ? "🎬 Video" : r.format === "term" ? "⌨️ Terminal" : `${r.frame_count} Frames`} · ${esc(r.username || "")}`}
            </div>
          </td>
          <td style="width:30px"><button class="taskbar-btn" data-del="${r.id}" title="Löschen">✕</button></td>
        </tr>`;
      }).join("");

      listEl.querySelectorAll("[data-rec]").forEach((tr) =>
        tr.addEventListener("click", (e) => {
          if (e.target.closest("[data-del]")) return;
          listEl.querySelectorAll("tr").forEach((x) => x.style.background = "");
          tr.style.background = "rgba(45,212,191,0.1)";
          loadRecording(tr.dataset.rec);
        })
      );
      listEl.querySelectorAll("[data-del]").forEach((btn) =>
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          if (!(await uiConfirm("Aufzeichnung löschen?", { okText: "Löschen", danger: true }))) return;
          try { await api.deleteRecording(btn.dataset.del); loadList(); }
          catch (err) { window.notify?.(err.message, "error"); }
        })
      );

      // Wurde die App mit einer bestimmten Aufzeichnung geöffnet (z.B. aus dem
      // Audit-Log), diese direkt auswählen und laden.
      if (preselectRecId) {
        const row = listEl.querySelector(`[data-rec="${preselectRecId}"]`);
        if (row) {
          listEl.querySelectorAll("tr").forEach((x) => x.style.background = "");
          row.style.background = "rgba(45,212,191,0.1)";
          row.scrollIntoView({ block: "nearest" });
        }
        loadRecording(preselectRecId);
        preselectRecId = null; // nur beim ersten Laden automatisch öffnen
      }
    } catch (e) {
      listEl.innerHTML = `<tr><td style="color:var(--danger)">${esc(e.message)}</td></tr>`;
    }
  }

  body.querySelector(`#rec-refresh-${win.key}`).addEventListener("click", loadList);
  registerCleanup(win.key, () => { stopPlayback(); releaseVideo(); disposeTerm(); });
  loadList();
}
