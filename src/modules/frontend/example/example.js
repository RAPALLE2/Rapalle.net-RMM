// Beispiel-Modul (Frontend): eine kleine App, die zeigt, wie es geht.
//
//  - render-Funktion bekommt (body, win): body = Inhalt des Fensters, win = Fenster-Info
//  - Core-Dateien werden mit ABSOLUTEN Pfaden importiert (/js/app/...)
//  - moduleRequest = authentifizierter Backend-Aufruf (JSON), Fehler -> Exception
//  - Wirft die App beim Zeichnen eine Ausnahme, zeigt NUR dieses Fenster einen Fehler;
//    das Modul wird deaktiviert und ein Crashbericht gespeichert (siehe Modul-App).

import { moduleRequest, getModules } from "/js/app/modules.js";
import { openWindow } from "/js/app/windowmanager.js";
import { esc } from "/js/app/utils.js";

export function renderExample(body, win) {
  // Absichtlicher Absturz beim Zeichnen (Fenster wird mit props.crash geoeffnet)
  if (win?.props?.crash) throw new Error("Absichtlicher Frontend-Absturz (Crash-Demo)");

  body.innerHTML = `
    <div style="padding:18px;display:flex;flex-direction:column;gap:12px">
      <h3 style="margin:0">🧪 Beispiel-Modul</h3>
      <p style="margin:0;color:var(--subtext);font-size:13px;line-height:1.5">
        Diese App gehört zum Modul <code>example</code>. Sie ruft einen Endpunkt des Backend-Teils auf
        und kann den Crashschutz demonstrieren. Anleitung: <code>src/modules/backend/example/GUIDE.md</code>.
      </p>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn-primary" id="ex-hello">Backend fragen</button>
        <button class="btn" id="ex-fe-crash">Frontend-Absturz testen</button>
        <button class="btn" id="ex-be-crash">Backend-Absturz testen</button>
      </div>
      <pre id="ex-out" style="margin:0;background:var(--panel-2);border-radius:8px;padding:10px;font-size:12px;min-height:70px;white-space:pre-wrap"></pre>
    </div>`;
  const out = body.querySelector("#ex-out");
  const show = (v) => { out.textContent = typeof v === "string" ? v : JSON.stringify(v, null, 2); };

  body.querySelector("#ex-hello").onclick = async () => {
    try { show(await moduleRequest("/api/example/hello")); }
    catch (e) { show("Fehler: " + e.message); }
  };
  // Neues Fenster, dessen render() absichtlich wirft -> fatal: Modul wird deaktiviert.
  body.querySelector("#ex-fe-crash").onclick = () =>
    openWindow({ key: "example-crash", appId: "example", title: "Absturz-Demo", props: { crash: true }, w: 420, h: 220 });
  // Der Endpunkt existiert nur, wenn die Teilfunktion "Absturz-Demo" eingeschaltet ist.
  body.querySelector("#ex-be-crash").onclick = async () => {
    try { await moduleRequest("/api/example/crash"); }
    catch (e) { show("Antwort: " + esc(e.message)); }
  };
  show({ aktiveModule: getModules().map((m) => m.id) });
}
