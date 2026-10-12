import { api } from "/js/app/api.js";
import { esc } from "/js/app/utils.js";
import { t } from "/js/app/i18n.js";

// Einstellungsmenue des Moduls "Audio-Player": Spotify-Anbindung (frueher in Einstellungen -> Allgemein).
export async function renderSettings(root, ctx) {
  const s = await api.getSettings();
  root.innerHTML = `<div>
        <h3 style="margin-top:24px">Spotify</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_spotify_hint")}
        </p>
        <div class="form-row">
          <label>Client-ID</label>
          <input id="ge-spotify-id" value="${esc(s.spotify_client_id ?? "")}" placeholder="z.B. 8a3f…" style="max-width:420px" />
        </div>
        <div style="background:var(--panel-2);border:1px solid var(--border);border-radius:8px;
                    padding:8px 10px;font-size:12px;color:var(--subtext);max-width:640px;line-height:1.55">
          <b style="color:var(--text)">${t("set_spotify_redirect")}</b>
          <div style="display:flex;gap:6px;align-items:center;margin:5px 0">
            <code id="ge-spotify-uri" style="flex:1;padding:4px 7px;background:var(--panel);
                  border:1px solid var(--border);border-radius:6px;overflow-wrap:anywhere"></code>
            <button class="btn" id="ge-spotify-copy" type="button"
                    title="${t("set_spotify_copy")}" style="flex:none">📋</button>
          </div>
          ${t("set_spotify_uri_hint")}<br>
          <b style="color:var(--text)">${t("tip")}:</b> ${t("set_spotify_both")}
          (<code id="ge-spotify-alt"></code>).<br>
          <span id="ge-spotify-warn"></span>
          ${t("set_spotify_exact")}
        </div>
        <div id="ge-error" class="form-error hidden"></div>
        <button class="btn-primary" id="ge-save" style="margin-top:8px">${t("save")}</button>
  </div>`;
    const spUri = root.querySelector("#ge-spotify-uri");
    const spWarn = root.querySelector("#ge-spotify-warn");
    const urlInput = root.querySelector("#ge-server-url");
    // Exakt die eingetragene URL (Spotify vergleicht zeichengenau) - nur
    // mehrfache Schrägstriche am Ende werden auf einen reduziert.
    const effRedirect = () => {
      const base = (urlInput?.value ?? s.server_url ?? "").trim();
      if (!base) return window.location.origin + "/";
      return base.endsWith("/") ? base.replace(/\/+$/, "") + "/" : base;
    };
    function refreshRedirect() {
      const uri = effRedirect();
      if (spUri) spUri.textContent = uri;
      const altEl = root.querySelector("#ge-spotify-alt");
      if (altEl) altEl.textContent = uri.endsWith("/") ? uri.slice(0, -1) : uri + "/";
      if (!spWarn) return;
      // Spotify erlaubt nur https:// oder die Loopback-Adresse 127.0.0.1.
      const loopback = /^http:\/\/127\.0\.0\.1(:\d+)?(\/|$)/.test(uri);
      // Den erlaubten Loopback-Vorschlag mit dem TATSAECHLICHEN Port dieser
      // Installation zeigen - "PORT" als Platzhalter musste der Benutzer sonst
      // selbst ersetzen.
      const port = window.location.port || (window.location.protocol === "https:" ? "443" : "80");
      const loopbackExample = `http://127.0.0.1:${port}/`;
      spWarn.innerHTML = (uri.startsWith("http://") && !loopback)
        ? `<span style="color:var(--warn,#f5a524)">⚠ ${t("set_spotify_bad_uri", { example: esc(loopbackExample) })}</span><br>`
        : "";
    }
    urlInput?.addEventListener("input", refreshRedirect);
    refreshRedirect();
    root.querySelector("#ge-spotify-copy")?.addEventListener("click", async () => {
      const uri = effRedirect();
      try {
        await navigator.clipboard.writeText(uri);
        window.notify?.(t("set_spotify_copied"), "success", 2000);
      } catch {
        window.notify?.(t("u_kopieren_nicht_moglich_bitte_manue") + uri, "info", 8000);
      }
    });
  root.querySelector("#ge-save").addEventListener("click", async () => {
    const err = root.querySelector("#ge-error"); err.classList.add("hidden");
    try {
      await api.updateSettings({ spotify_client_id: (root.querySelector("#ge-spotify-id")?.value || "").trim() });
      window.notify?.(t("general_saved"), "success");
    } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
  });
}
