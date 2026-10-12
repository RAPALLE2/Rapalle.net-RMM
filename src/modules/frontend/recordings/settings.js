import { api } from "/js/app/api.js";
import { esc } from "/js/app/utils.js";
import { t } from "/js/app/i18n.js";

// Einstellungsmenue des Moduls "Aufzeichnungen" (frueher in Einstellungen -> Allgemein).
export async function renderSettings(root, ctx) {
  const s = await api.getSettings();
  root.innerHTML = `<div>
        <h3 style="margin-top:24px">${t("set_recording")}</h3>
        <p style="color:var(--subtext);font-size:13px">
          ${t("set_recording_hint")}
        </p>
        <div class="form-row">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="ge-rec-enabled" ${(s.recording_enabled ?? "1") === "1" ? "checked" : ""} />
            ${t("set_recording_on")}
          </label>
        </div>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>${t("set_screen_quality")}</label>
            <input type="number" min="1" max="100" id="ge-screen-q" value="${esc(String(s.screen_record_quality ?? 40))}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>${t("set_screen_fps")}</label>
            <input type="number" min="1" max="30" id="ge-screen-fps" value="${esc(String(s.screen_record_fps ?? 5))}" />
          </div>
        </div>
        <div style="display:flex;gap:12px">
          <div class="form-row" style="flex:1">
            <label>${t("set_guac_quality")}</label>
            <input type="number" min="1" max="95" id="ge-guac-q" value="${esc(String(s.guac_record_quality ?? 50))}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>Guacamole-Bilder/Sek.</label>
            <input type="number" min="1" max="30" id="ge-guac-fps" value="${esc(String(s.guac_record_fps ?? 8))}" />
          </div>
          <div class="form-row" style="flex:1">
            <label>Guacamole-Skalierung (0.1–1.0)</label>
            <input type="number" min="0.1" max="1" step="0.05" id="ge-guac-scale" value="${esc(String(s.guac_record_scale ?? 0.75))}" />
          </div>
        </div>
        <div id="ge-error" class="form-error hidden"></div>
        <button class="btn-primary" id="ge-save" style="margin-top:8px">${t("save")}</button>
  </div>`;
  root.querySelector("#ge-save").addEventListener("click", async () => {
    const err = root.querySelector("#ge-error"); err.classList.add("hidden");
    const val = (id) => root.querySelector(`#${id}`)?.value;
    try {
      await api.updateSettings({
        recording_enabled: root.querySelector("#ge-rec-enabled").checked ? "1" : "0",
        screen_record_quality: parseInt(val("ge-screen-q"), 10) || 40,
        screen_record_fps: parseInt(val("ge-screen-fps"), 10) || 5,
        guac_record_quality: parseInt(val("ge-guac-q"), 10) || 50,
        guac_record_fps: parseInt(val("ge-guac-fps"), 10) || 8,
        guac_record_scale: parseFloat(val("ge-guac-scale")) || 0.75,
      });
      window.notify?.(t("general_saved"), "success");
    } catch (e) { err.textContent = e.message; err.classList.remove("hidden"); }
  });
}
