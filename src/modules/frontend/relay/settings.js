import { api } from "/js/app/api.js";
import { esc } from "/js/app/utils.js";
import { t } from "/js/app/i18n.js";

// Einstellungsmenue des Moduls "Explorer-Relay": FTP/SFTP/WebDAV-Zugang
// (die Deployment-Seite hat jetzt ein eigenes Modul "deployment")
// (frueher in Einstellungen -> Allgemein).
export async function renderSettings(root, ctx) {
  const s = await api.getSettings();
  const draw = () => renderSettings(root, ctx);       // Menue neu zeichnen
  root.innerHTML = `<div>
        <div data-adminsec>
        <h3 style="margin-top:24px">Relay</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_relay_hint")}
        </p>
        <div id="ge-relay" style="font-size:13px;color:var(--subtext)">${t("loading")}</div>
        </div>
  </div>`;
    // ---------------- Relay: FTP-Zugang -----------------------------------
    (async () => {
      const box = root.querySelector("#ge-relay");
      if (!box) return;
      let cfg;
      try {
        cfg = await api.relayFtpConfig();
      } catch {
        box.textContent = t("set_ftp_unsupported");
        return;
      }
      const draw = () => {
        // WebDAV ist ein eigener Schalter (HTTP, client-first) und laesst sich
        // frei mit FTP/SFTP kombinieren. FTP und SFTP dagegen schliessen sich
        // gegenseitig aus, weil bei beiden der Server zuerst spricht - deshalb
        // eine Auswahl mit drei Moeglichkeiten statt zweier Haekchen.
        const opt = (val, label, hint, disabled = false) => `
          <label style="display:flex;gap:9px;align-items:flex-start;padding:7px 8px;
                        border-radius:8px;cursor:${disabled ? "not-allowed" : "pointer"};
                        opacity:${disabled ? ".55" : "1"};
                        background:${cfg.mode === val ? "var(--panel-2)" : "transparent"}">
            <input type="radio" name="ge-filemode" value="${val}" style="margin-top:3px"
                   ${cfg.mode === val ? "checked" : ""} ${disabled ? "disabled" : ""} />
            <span>
              <span style="color:var(--text)">${label}</span>
              <div style="font-size:11.5px;margin-top:2px">${hint}</div>
            </span>
          </label>`;

        box.innerHTML = `
          <label style="display:flex;gap:9px;align-items:flex-start;padding:7px 8px;
                        border-radius:8px;cursor:pointer;margin-bottom:6px;
                        background:${cfg.webdav ? "var(--panel-2)" : "transparent"}">
            <input type="checkbox" id="ge-webdav" style="margin-top:3px" ${cfg.webdav ? "checked" : ""} />
            <span>
              <span style="color:var(--text)">${t("set_webdav_label")}</span>
              <div style="font-size:11.5px;margin-top:2px">
                <code>http://&lt;server&gt;:${cfg.port}/dav/</code> · ${t("set_webdav_hint")}
              </div>
            </span>
          </label>

          <div style="margin:10px 0 4px;color:var(--text)">${t("set_extra_file_access")}</div>
          ${opt("off", t("set_none_word"), t("set_only_checked"))}
          ${opt("ftp", `FTP (Port ${cfg.port})`,
                `<code>ftp://&lt;server&gt;:${cfg.port}</code> · ${t("set_ftp_oneport")}`)}
          ${opt("sftp", `SFTP (Port ${cfg.port})`,
                cfg.sftp_available
                  ? `<code>sftp://&lt;server&gt;:${cfg.port}</code> · ${t("set_sftp_oneport")}`
                  : esc(cfg.sftp_reason),
                !cfg.sftp_available)}
          <div style="margin-top:8px;color:var(--warn,#f5a524)">⚠ ${esc(cfg.note)}</div>
          ${cfg.restart_pending ? `
            <div style="margin-top:6px;padding:8px;border:1px solid var(--warn,#f5a524);
                        border-radius:8px;color:var(--warn,#f5a524)">
              ⚠ ${t("set_listener_pending", { mode: esc(cfg.mode.toUpperCase()) })}
              ${cfg.listener_error ? `<div style="margin-top:4px;font-size:11.5px">${t("set_last_error")}: ${esc(cfg.listener_error)}</div>` : ""}
            </div>` : ""}
          ${(cfg.mode === "ftp" && !cfg.advertise_host) ? `
            <div style="margin-top:6px;font-size:11.5px">
              ${t("set_ftp_nat_hint")}
            </div>` : ""}
          <div style="margin-top:4px">${t("set_login_dashboard_creds")}</div>`;

        // Nach dem Speichern: Neustart gleich oder später? FTP/SFTP wirken erst
        // danach, weil dabei die Weiche vor dem Port auf-/abgebaut wird.
        const askRestart = async (res) => {
          if (!res || !res.needs_restart) {
            window.notify?.(res?.note || t("saved"), "success", 4000);
            return;
          }
          if (!cfg.may_restart) {
            window.notify?.(
              t("set_saved_needs_restart_admin"),
              "warn", 12000);
            return;
          }
          const { uiChoice } = await import("../app/utils.js");
          const choice = await uiChoice(t("set_restart_now_q"), [
            { label: t("set_restart_now"), value: "now" },
            { label: t("set_restart_later"), value: "later" },
          ], { description: t("set_restart_desc") });
          if (choice !== "now") {
            window.notify?.(t("set_saved_next_restart"), "info", 8000);
            return;
          }
          try {
            await api.restartBackend();
            window.notify?.(t("set_backend_restarting"), "success", 8000);
            setTimeout(() => location.reload(), 6000);
          } catch (err) {
            window.notify?.(t("set_restart_failed") + ": " + err.message
              + " — " + t("set_ask_admin_restart"), "error", 12000);
          }
        };

        box.querySelectorAll("[name=ge-filemode]").forEach((r) =>
          r.addEventListener("change", async (e) => {
            const val = e.currentTarget.value;
            const before = cfg.mode;
            try {
              const res = await api.relayFtpMode(val);
              cfg.mode = res.mode;
              draw();
              await askRestart(res);
            } catch (err) {
              cfg.mode = before;
              draw();
              window.notify?.(err.message, "error", 9000);
            }
          }));

        box.querySelector("#ge-webdav")?.addEventListener("change", async (e) => {
          const on = e.currentTarget.checked;
          const before = cfg.webdav;
          try {
            const res = await api.relayFtpMode(cfg.mode, on);
            cfg.webdav = res.webdav;
            draw();
            // Reine WebDAV-Änderung braucht keinen Neustart -> nur Bestätigung.
            await askRestart(res);
          } catch (err) {
            cfg.webdav = before;
            draw();
            window.notify?.(err.message, "error", 9000);
          }
        });
      };
      draw();
    })();
    // Deployment-Seite: Modul "deployment" (src/modules/frontend/deployment/settings.js)
}
