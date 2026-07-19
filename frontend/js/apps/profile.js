// apps/profile.js
// ---------------
// Persönliche Einstellungen des eingeloggten Benutzers: Anzeigename, Sprache
// (DE/EN), Theme (Dark/Light) und Passwortänderung.

import { state } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";
import { applyTheme, applyAccent, ACCENT_PALETTES } from "../theme.js";
import { setLanguage, applyStaticTranslations } from "../i18n_apply.js";
import { renderSidebar } from "../sidebar.js";
import { renderMainContent } from "../panel.js";
import { getDashEdit, setDashEdit, scheduleSave, getRestorePrefs, setRestorePrefs } from "../persist.js";

export function renderProfile(body, win) {
  const u = state.user;
  const _rp = getRestorePrefs();
  body.innerHTML = `
    <div class="settings-section">
      <h3>Profil</h3>
      <div class="form-row">
        <label>Anzeigename</label>
        <input type="text" id="pr-name" value="${esc(u.display_name)}" />
      </div>
      <div class="form-row">
        <label>Sprache</label>
        <select id="pr-lang">
          <option value="de" ${u.language === "de" ? "selected" : ""}>Deutsch</option>
          <option value="en" ${u.language === "en" ? "selected" : ""}>English</option>
        </select>
      </div>
      <div class="form-row">
        <label>Design</label>
        <select id="pr-theme">
          <option value="dark" ${u.theme === "dark" ? "selected" : ""}>Dunkel</option>
          <option value="light" ${u.theme === "light" ? "selected" : ""}>Hell</option>
        </select>
      </div>
      <div class="form-row">
        <label>Farbpalette</label>
        <div id="pr-accents" style="display:flex;flex-wrap:wrap;gap:10px;margin-top:2px">
          ${Object.entries(ACCENT_PALETTES).map(([key, p]) => `
            <button type="button" class="accent-swatch ${(u.accent || "teal") === key ? "selected" : ""}"
              data-accent="${key}" title="${esc(p.name)}"
              style="background:linear-gradient(135deg, ${p.accent}, ${p.accent2})"></button>
          `).join("")}
        </div>
      </div>
      <div class="form-row">
        <label>Symbole</label>
        <select id="pr-icons">
          <option value="svg">SVG-Icons (empfohlen, auf jedem System gleich)</option>
          <option value="emoji">System-Emojis</option>
        </select>
      </div>
      <p style="color:var(--subtext);font-size:12px;max-width:520px;margin-top:2px">
        SVG-Icons sehen auf Windows, Linux, macOS und Android identisch aus.
        System-Emojis nutzen die Emoji-Schrift des Geräts (kann auf Linux/Android
        fehlen und als leere Kästchen erscheinen).
      </p>
      <button class="btn-primary" id="pr-save" style="margin-top:6px">Profil speichern</button>

      <h3 style="margin-top:26px">Dashboard</h3>
      <div class="form-row" style="align-items:center">
        <label>Layout-Bearbeitung</label>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
          <input type="checkbox" id="pr-dashedit" ${getDashEdit() ? "checked" : ""} />
          Ansichten frei verschieben, in Größe ändern & Ordner anpassen
        </label>
      </div>
      <p style="color:var(--subtext);font-size:12px;max-width:520px;margin-top:2px">
        Ist die Bearbeitung an, kannst du in der Client-Ansicht Status, Aktionen und
        Übersicht-Ordner per Ziehen anordnen, ihre Breite ziehen, weitere Ordner
        anlegen und Sub-Ansichten (Metrics/Notes/Disk) zwischen Ordnern verschieben.
        Über das ↗️-Symbol lässt sich jeder Baustein als eigenes Fenster herauslösen
        (auch ohne Bearbeitung). Clients kannst du aus der Seitenleiste direkt auf
        die Arbeitsfläche ziehen.
      </p>

      <h3 style="margin-top:26px">Nach dem Anmelden</h3>
      <p style="color:var(--subtext);font-size:12px;max-width:520px;margin-top:2px">
        Lege fest, was beim erneuten Anmelden wiederhergestellt wird. Ist ein Punkt
        deaktiviert, startest du an dieser Stelle „sauber" vom Dashboard.
      </p>
      <div class="form-row" style="align-items:center">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
          <input type="checkbox" id="pr-restore-client" ${_rp.client ? "checked" : ""} />
          Zuletzt geöffneten Client wiederherstellen
        </label>
      </div>
      <div class="form-row" style="align-items:center">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
          <input type="checkbox" id="pr-restore-folder" ${_rp.folder ? "checked" : ""} />
          Zuletzt geöffnete Ordner (Seitenleiste) wiederherstellen
        </label>
      </div>
      <div class="form-row" style="align-items:center">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
          <input type="checkbox" id="pr-restore-apps" ${_rp.apps ? "checked" : ""} />
          Zuletzt geöffnete Apps/Fenster wiederherstellen
        </label>
      </div>

      <h3 style="margin-top:26px">Passwort ändern</h3>
      ${u.auth_realm ? `
      <p style="color:var(--subtext);font-size:13px;max-width:520px">
        🔒 Dein Konto wird zentral über das Verzeichnis (AD/LDAP/SSO) verwaltet.
        Das Passwort kann nur dort geändert werden — nicht im RMM.
      </p>
      ` : `
      <div class="form-row">
        <label>Aktuelles Passwort</label>
        <input type="password" id="pr-cur" />
      </div>
      <div class="form-row">
        <label>Neues Passwort (min. 8 Zeichen)</label>
        <input type="password" id="pr-new" />
      </div>
      <div id="pr-msg" style="margin:8px 0;font-size:13px"></div>
      <button class="btn-primary" id="pr-changepw" style="margin-top:6px">Passwort ändern</button>
      `}
    </div>
  `;

  // Dashboard-Bearbeitung an/aus (sofort wirksam; pro Benutzer gespeichert)
  body.querySelector("#pr-dashedit")?.addEventListener("change", (e) => {
    setDashEdit(e.target.checked);
    scheduleSave(state);
    renderMainContent();   // Client-Ansicht mit/ohne Edit-Werkzeuge neu zeichnen
    // Offene Dashboard-Fenster über den Umschalter informieren.
    try { window.dispatchEvent(new CustomEvent("dashedit-changed")); } catch {}
  });
  // Umgekehrte Richtung: Wird die Bearbeitung anderswo beendet (z.B. Button
  // "✓ Bearbeiten beenden" in der Client-Toolbar), Haken hier mitziehen.
  const _syncEditCheckbox = () => {
    const cb = body.querySelector("#pr-dashedit");
    if (!document.body.contains(body)) {
      window.removeEventListener("dashedit-changed", _syncEditCheckbox);
      return;
    }
    if (cb) cb.checked = getDashEdit();
  };
  window.addEventListener("dashedit-changed", _syncEditCheckbox);

  // Live-Vorschau: Theme sofort umschalten, wenn im Dropdown geändert
  body.querySelector("#pr-theme").addEventListener("change", (e) => applyTheme(e.target.value));

  // Symbole: SVG-Icons vs. System-Emojis — wirkt sofort, pro Gerät gespeichert
  import("../icons.js").then(({ getIconMode, setIconMode }) => {
    const sel = body.querySelector("#pr-icons");
    if (!sel) return;
    sel.value = getIconMode();
    sel.addEventListener("change", () => setIconMode(sel.value));
  });

  // Farbpalette wählen: markiert die Auswahl und zeigt sie sofort als Vorschau
  let selectedAccent = u.accent || "teal";
  body.querySelectorAll(".accent-swatch").forEach((sw) =>
    sw.addEventListener("click", () => {
      selectedAccent = sw.dataset.accent;
      body.querySelectorAll(".accent-swatch").forEach((s) => s.classList.remove("selected"));
      sw.classList.add("selected");
      applyAccent(selectedAccent);  // Live-Vorschau
    })
  );

  // Wiederherstellungs-Einstellungen: sofort speichern (pro Benutzer, lokal).
  const _wireRestore = (id, keyName) => {
    body.querySelector(id)?.addEventListener("change", (e) => {
      setRestorePrefs({ [keyName]: e.target.checked });
      scheduleSave(state);
    });
  };
  _wireRestore("#pr-restore-client", "client");
  _wireRestore("#pr-restore-folder", "folder");
  _wireRestore("#pr-restore-apps", "apps");

  body.querySelector("#pr-save").addEventListener("click", async () => {
    const updated = await api.updateProfile({
      display_name: body.querySelector("#pr-name").value,
      language: body.querySelector("#pr-lang").value,
      theme: body.querySelector("#pr-theme").value,
      accent: selectedAccent,
    });
    state.user = updated;
    applyTheme(updated.theme);
    applyAccent(updated.accent || "teal");
    setLanguage(updated.language);
    applyStaticTranslations();       // statische HTML-Texte übersetzen
    renderSidebar();                 // Sidebar in neuer Sprache
    renderMainContent();             // Hauptbereich in neuer Sprache
    document.getElementById("user-menu-name").textContent = updated.display_name;
    const okMsg = body.querySelector("#pr-msg");
    if (okMsg) okMsg.innerHTML = `<span style="color:var(--accent)">✓</span>`;
  });

  body.querySelector("#pr-changepw")?.addEventListener("click", async () => {
    const msg = body.querySelector("#pr-msg");
    try {
      await api.changePassword(
        body.querySelector("#pr-cur").value,
        body.querySelector("#pr-new").value
      );
      msg.innerHTML = `<span style="color:var(--accent)">Passwort geändert.</span>`;
      body.querySelector("#pr-cur").value = "";
      body.querySelector("#pr-new").value = "";
    } catch (e) {
      msg.innerHTML = `<span style="color:var(--danger)">${esc(e.message)}</span>`;
    }
  });
}
