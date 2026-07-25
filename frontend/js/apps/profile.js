// apps/profile.js
// ---------------
// Persönliche Einstellungen des eingeloggten Benutzers: Anzeigename, Sprache
// (DE/EN), Theme (Dark/Light) und Passwortänderung.

import { state } from "../state.js";
import { hasGlobalPerm } from "../state.js";
import { api } from "../api.js";
import { esc } from "../utils.js";
import { applyTheme, applyAccent, ACCENT_PALETTES } from "../theme.js";
import { setLanguage, applyStaticTranslations } from "../i18n_apply.js";
import { renderSidebar } from "../sidebar.js";
import { renderMainContent } from "../panel.js";
import { getDashEdit, setDashEdit, scheduleSave, getRestorePrefs, setRestorePrefs } from "../persist.js";
import { t } from "../i18n.js";

export function renderProfile(body, win) {
  const u = state.user;
  const _rp = getRestorePrefs();
  const mayEditName = hasGlobalPerm("edit_profile_name");
  const mayCustomizeDash = hasGlobalPerm("customize_dashboard");
  const mayRestore = hasGlobalPerm("restore_session");
  // Silent-Modus (Remote-Bildschirm ohne Anfrage): Abschnitt nur anzeigen,
  // wenn das Recht global, auf mindestens einem Client oder als Admin vorliegt.
  const maySilent = state.perms?.admin === true
    || hasGlobalPerm("screen_silent")
    || Object.values(state.perms?.clients || {}).some((m) => m && m.c_screen_silent);
  body.innerHTML = `
    <div class="settings-section">
      <h3>Profil</h3>
      <div class="form-row">
        <label>Anzeigename</label>
        <input type="text" id="pr-name" value="${esc(u.display_name)}" ${mayEditName ? "" : "disabled title=\"Keine Berechtigung\""} />
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

      <h3 style="margin-top:26px" ${mayCustomizeDash ? "" : 'hidden'}>Dashboard</h3>
      <div class="form-row" style="align-items:center" ${mayCustomizeDash ? "" : 'hidden'}>
        <label>Layout-Bearbeitung</label>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
          <input type="checkbox" id="pr-dashedit" ${getDashEdit() ? "checked" : ""} />
          Ansichten frei verschieben, in Größe ändern & Ordner anpassen
        </label>
      </div>
      <p style="color:var(--subtext);font-size:12px;max-width:520px;margin-top:2px" ${mayCustomizeDash ? "" : 'hidden'}>
        Ist die Bearbeitung an, kannst du in der Client-Ansicht Status, Aktionen und
        Übersicht-Ordner per Ziehen anordnen, ihre Breite ziehen, weitere Ordner
        anlegen und Sub-Ansichten (Metrics/Notes/Disk) zwischen Ordnern verschieben.
        Über das ↗️-Symbol lässt sich jeder Baustein als eigenes Fenster herauslösen
        (auch ohne Bearbeitung). Clients kannst du aus der Seitenleiste direkt auf
        die Arbeitsfläche ziehen.
      </p>

      <h3 style="margin-top:26px" ${mayRestore ? "" : "hidden"}>Nach dem Anmelden</h3>
      <p style="color:var(--subtext);font-size:12px;max-width:520px;margin-top:2px" ${mayRestore ? "" : "hidden"}>
        Lege fest, was beim erneuten Anmelden wiederhergestellt wird. Ist ein Punkt
        deaktiviert, startest du an dieser Stelle „sauber" vom Dashboard.
      </p>
      <div class="form-row" style="align-items:center" ${mayRestore ? "" : "hidden"}>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
          <input type="checkbox" id="pr-restore-client" ${_rp.client ? "checked" : ""} />
          Zuletzt geöffneten Client wiederherstellen
        </label>
      </div>
      <div class="form-row" style="align-items:center" ${mayRestore ? "" : "hidden"}>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
          <input type="checkbox" id="pr-restore-folder" ${_rp.folder ? "checked" : ""} />
          Zuletzt geöffnete Ordner (Seitenleiste) wiederherstellen
        </label>
      </div>
      <div class="form-row" style="align-items:center" ${mayRestore ? "" : "hidden"}>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--subtext);font-size:13px">
          <input type="checkbox" id="pr-restore-apps" ${_rp.apps ? "checked" : ""} />
          Zuletzt geöffnete Apps/Fenster wiederherstellen
        </label>
      </div>

      <h3 style="margin-top:26px" ${maySilent ? "" : "hidden"}>Remote-Bildschirm</h3>
      <style>
        /* Toggle-Schalter für den Silent-Modus */
        .pr-toggle { position:relative;display:inline-block;width:46px;height:24px;flex:none }
        .pr-toggle input { opacity:0;width:0;height:0 }
        .pr-toggle .knob { position:absolute;inset:0;background:#33405a;border-radius:24px;
          transition:background .15s;cursor:pointer }
        .pr-toggle .knob::before { content:"";position:absolute;left:3px;top:3px;width:18px;height:18px;
          border-radius:50%;background:#fff;transition:transform .15s }
        .pr-toggle input:checked + .knob { background:#3ecf8e }
        .pr-toggle input:checked + .knob::before { transform:translateX(22px) }
        .pr-toggle input:disabled + .knob { opacity:.45;cursor:not-allowed }
      </style>
      <div class="form-row" style="align-items:center" ${maySilent ? "" : "hidden"}>
        <label>Silent-Modus</label>
        <label class="pr-toggle" title="${t("u_nachste_remote_sitzung_ohne_anfrag")}">
          <input type="checkbox" id="pr-silent" disabled />
          <span class="knob"></span>
        </label>
        <span id="pr-silent-state" style="font-size:13px;color:var(--subtext)">…</span>
      </div>
      <p style="color:var(--subtext);font-size:12px;max-width:520px;margin-top:2px" ${maySilent ? "" : "hidden"}>
        Ist der Modus aktiv, wird bei der nächsten Remote-Bildschirm-Sitzung der
        Zustimmungs-Dialog am Gerät übersprungen. Nach dem Verbindungsaufbau
        schaltet sich der Modus automatisch wieder aus. Nützlich z.B. wenn jemand
        angemeldet, aber AFK ist und dringend etwas erledigt werden muss. Jede
        Nutzung wird im Audit-Log protokolliert.
      </p>
      <div id="pr-silent-msg" style="font-size:12px;color:var(--subtext)" ${maySilent ? "" : "hidden"}></div>

      <h3 style="margin-top:26px">Terminal</h3>
      <div class="form-row" style="align-items:center">
        <label>Rechtsklick</label>
        <select id="pr-term-rc" style="max-width:340px">
          <option value="direct">Direkt: markiert = Kopieren, sonst Einfügen (PuTTY-Stil)</option>
          <option value="menu">Kontextmenü: Kopieren / Einfügen / Alles kopieren</option>
        </select>
      </div>
      <p style="color:var(--subtext);font-size:12px;max-width:520px;margin-top:2px">
        Shortcuts funktionieren immer: <b>Strg+Shift+C</b> kopieren (bzw. Strg+C
        bei Markierung), <b>Strg+V</b> einfügen, <b>Strg+Einfg</b>/<b>Shift+Einfg</b>
        klassisch. Die Einstellung wirkt sofort, auch in offenen Terminals.
      </p>

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

  // ---- Terminal: Rechtsklick-Verhalten (sofort wirksam, serverseitig gesynct) ----
  const termRc = body.querySelector("#pr-term-rc");
  if (termRc) {
    termRc.value = localStorage.getItem("rmm_term_rightclick") || "direct";
    termRc.addEventListener("change", () => {
      try { localStorage.setItem("rmm_term_rightclick", termRc.value); } catch {}
      import("../persist.js").then((m) => m.syncToServerSoon()).catch(() => {});
      window.notify?.(termRc.value === "menu"
        ? t("u_terminal_rechtsklick_kontextmenu")
        : t("u_terminal_rechtsklick_direkt_kopier"), "success", 2500);
    });
  }

  // Dashboard-Bearbeitung an/aus (sofort wirksam; pro Benutzer gespeichert)
  body.querySelector("#pr-dashedit")?.addEventListener("change", (e) => {
    setDashEdit(e.target.checked);
    scheduleSave(state);
    renderMainContent();   // Client-Ansicht mit/ohne Edit-Werkzeuge neu zeichnen
    // Offene Dashboard-Fenster über den Umschalter informieren.
    try { window.dispatchEvent(new CustomEvent("dashedit-changed")); } catch {}
  });

  // ---- Silent-Modus (Remote-Bildschirm ohne Anfrage) ----
  // Aktuellen Stand vom Server laden, dann Checkbox freischalten. Beim
  // Umschalten sofort speichern - der Modus gilt nur für die NÄCHSTE Sitzung
  // und schaltet sich danach serverseitig selbst wieder aus.
  if (maySilent) {
    const silentCb = body.querySelector("#pr-silent");
    const silentMsg = body.querySelector("#pr-silent-msg");
    const silentState = body.querySelector("#pr-silent-state");
    const setStateText = (on) => {
      if (silentState) silentState.textContent = on
        ? t("u_an_nachste_sitzung_ohne_anfrage_ei")
        : t("u_aus_sitzungen_fragen_normal_am_ger");
    };
    const refresh = async () => {
      try {
        const st = await api.getSilentScreen();
        if (!document.body.contains(body)) return;
        silentCb.checked = !!st.enabled;
        setStateText(silentCb.checked);
        // Server-Antwort ist die Wahrheit: Nur freischalten, wenn das Recht
        // auch serverseitig vorliegt (deckt veralteten Rechte-Cache ab).
        silentCb.disabled = st.allowed === false;
        if (st.allowed === false && silentMsg) {
          silentMsg.textContent = "Dir fehlt das Recht 'Remote-Bildschirm ohne Anfrage'.";
        }
      } catch (e) {
        if (silentMsg) silentMsg.textContent =
          `Status nicht ladbar: ${e.message} - läuft das Backend mit der aktuellen Version (auth_routes.py/sockets.py)?`;
      }
    };
    refresh();

    // Wird der Silent-Modus durch den Start einer Remote-Sitzung VERBRAUCHT,
    // meldet das Backend das live -> Toggle geht sichtbar wieder AUS.
    const onConsumed = (e) => {
      if (!document.body.contains(body)) {
        window.removeEventListener("silent-screen-consumed", onConsumed);
        return;
      }
      if (e.detail?.username && e.detail.username !== state.user?.username) return;
      silentCb.checked = false;
      setStateText(false);
      if (silentMsg) silentMsg.textContent =
        `Silent-Modus wurde für die Sitzung${e.detail?.client ? ` auf "${e.detail.client}"` : ""} genutzt und ist wieder AUS.`;
    };
    window.addEventListener("silent-screen-consumed", onConsumed);

    // Zusätzlich LOKAL: Sobald DIESER Browser eine Remote-Session startet
    // (vnc.js feuert "screen-session-started"), geht der Toggle sofort aus -
    // ohne auf das Backend-Event warten zu müssen. Kurz danach wird der echte
    // Stand vom Server nachgeladen (falls der Modus z.B. mangels Recht gar
    // nicht verbraucht wurde, springt der Toggle korrekt wieder an).
    const onSessionStart = () => {
      if (!document.body.contains(body)) {
        window.removeEventListener("screen-session-started", onSessionStart);
        return;
      }
      if (silentCb.checked) {
        silentCb.checked = false;
        setStateText(false);
        if (silentMsg) silentMsg.textContent =
          "Remote-Sitzung gestartet - Silent-Modus ist wieder AUS.";
      }
      setTimeout(refresh, 1200);   // Server-Wahrheit nachziehen
    };
    window.addEventListener("screen-session-started", onSessionStart);

    // Fallback ohne Live-Verbindung: Beim Zurückkehren ins Fenster neu laden.
    window.addEventListener("focus", () => {
      if (document.body.contains(body)) refresh();
    });
    silentCb?.addEventListener("change", async (e) => {
      const on = e.target.checked;
      silentCb.disabled = true;
      try {
        await api.setSilentScreen(on);
        setStateText(on);
        if (silentMsg) silentMsg.textContent = on
          ? t("u_aktiv_die_nachste_remote_sitzung_s")
          : "Silent-Modus deaktiviert.";
        window.notify?.(on ? "Silent-Modus aktiviert (einmalig)" : "Silent-Modus deaktiviert",
                        on ? "success" : "info");
      } catch (err) {
        e.target.checked = !on;   // zurückrollen
        setStateText(!on);
        if (silentMsg) silentMsg.textContent = `Fehler: ${err.message}`;
      } finally {
        silentCb.disabled = false;
      }
    });
  }
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
