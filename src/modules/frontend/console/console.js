// console.js (Modul "console") - RMM-Konsole im Dashboard / in der Client-App
// --------------------------------------------------------------------------
// Terminal (MiniTerm) <-> WebSocket /api/console/ws. Die Konsole selbst laeuft im Backend
// (shell.py, dieselbe wie per SSH). Kopfzeile zeigt den SSH-Zugang, falls eingeschaltet.
import { getToken } from "/js/app/api.js";
import { esc } from "/js/app/utils.js";

export function renderConsole(body, win) {
  const via = win?.props?.via === "client" ? "client" : "";
  body.innerHTML = `
    <div style="height:100%;display:flex;flex-direction:column;min-height:0;background:#0b0f14">
      <div style="display:flex;gap:8px;align-items:center;padding:5px 10px;background:var(--panel);border-bottom:1px solid var(--border);font-size:12px">
        <span data-st style="flex:1;color:var(--subtext)">Verbinde …</span>
        <span data-ssh style="color:var(--subtext)"></span>
        <button class="btn btn-sm" data-re title="Neu verbinden">⟳</button>
      </div>
      <div data-term style="flex:1;min-height:0"></div>
    </div>`;
  const st = body.querySelector("[data-st]");
  const host = body.querySelector("[data-term]");
  let ws = null, term = null;

  fetch("/api/console/info", { headers: { Authorization: "Bearer " + (getToken() || "") } })
    .then((r) => r.ok ? r.json() : null).then((i) => {
      if (i?.ssh?.port) body.querySelector("[data-ssh]").innerHTML =
        `SSH: <code>ssh &lt;benutzer&gt;@${esc(location.hostname)} -p ${i.ssh.port}</code>`;
    }).catch(() => {});

  async function connect() {
    try { ws?.close(); } catch { /* egal */ }
    try { term?.dispose(); } catch { /* egal */ }
    host.innerHTML = "";
    const { MiniTerm } = await import("/js/feature/miniterm.js");
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/console/ws${via ? "?via=client" : ""}`;
    ws = new WebSocket(url, ["rmm-console", `rmmtoken.${getToken() || ""}`]);
    term = new MiniTerm(host, {
      onData: (d) => { if (ws.readyState === 1) ws.send(JSON.stringify({ t: "i", d })); },
      onResize: (c, r) => { if (ws.readyState === 1) ws.send(JSON.stringify({ t: "r", c, r })); },
    });
    st.textContent = "Verbinde …";
    ws.onopen = () => { st.textContent = "Verbunden"; term.fit(); ws.send(JSON.stringify({ t: "r", c: term.cols, r: term.rows })); term.focus(); };
    ws.onmessage = (e) => term.write(e.data);
    ws.onclose = (e) => {
      st.textContent = e.code === 4403 ? "Kein Zugriff – Recht „RMM-Konsole benutzen“ fehlt" : "Getrennt – ⟳ verbindet neu";
      st.style.color = e.code === 4403 ? "var(--danger)" : "var(--subtext)";
    };
  }
  body.querySelector("[data-re]").addEventListener("click", connect);
  connect();
  return () => { try { ws?.close(); } catch { /* egal */ } try { term?.dispose(); } catch { /* egal */ } };
}
