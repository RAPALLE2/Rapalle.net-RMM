// MainActivity.java - RAPALLE.net RMM Client fuer Android (ohne Fremdbibliotheken)
// ------------------------------------------------------------------------------
// Ablauf wie bei der Desktop-App:
//   gespeicherter Geraetetoken? -> POST /api/client/session -> Dashboard (WebView)
//   sonst                       -> assets/login.html (Anmeldung inkl. SSO + 2FA)
// Die Sitzung geht per "#client-token=..." ans Dashboard (dort sofort aus der
// Adresse entfernt). Gespeichert wird nur der Geraetetoken - im privaten
// App-Speicher, auf den keine andere App zugreifen kann. Kein Passwort.
//
// Menue (oben rechts): Dashboard, Remote-Shell, Fernbild (Remotedesktop / VNC),
// Relay-Zugang, Abmelden. Fernbild startet per Intent die App
// "Remote Desktop" (Microsoft) bzw. einen VNC-Viewer (z.B. bVNC) mit einer
// Weiterleitung, die nur von diesem Geraet aus erreichbar ist.
//
// Gebaut wird mit src/client/build_installers.py (aapt, javac, dx, apksigner).

package net.rapalle.rmmclient;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.view.Menu;
import android.view.MenuItem;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.TextView;
import android.webkit.JavascriptInterface;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSession;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

public class MainActivity extends Activity {
    static final String VERSION = "1.0.0";
    static final long REFRESH_MS = 6L * 3600 * 1000;
    static final int REQ_FILE = 42;

    WebView web;
    SharedPreferences prefs;
    final Handler ui = new Handler(Looper.getMainLooper());
    String session = "";              // aktuelles JWT (nur im Speicher)
    long sessionAt = 0;
    String loginMessage = "";
    ValueCallback<Uri[]> fileCallback;

    // ================================================================ Start
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("rmm", Context.MODE_PRIVATE);
        web = new WebView(this);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE);
        s.setUserAgentString(s.getUserAgentString() + " RapalleClient/" + VERSION);
        web.addJavascriptInterface(new Bridge(), "RMM");
        web.setWebViewClient(new WebViewClient() {
            // (String-Variante: laeuft auf allen Android-Versionen, auch den neuesten)
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, String url) {
                Uri u = Uri.parse(url);
                String server = prefs.getString("server", "");
                if (url.startsWith("file:") || url.startsWith("blob:") || (!server.isEmpty() && url.startsWith(server))) {
                    return false;                       // eigene Seiten in der App
                }
                try { startActivity(new Intent(Intent.ACTION_VIEW, u)); } catch (Exception ignored) { }
                return true;                            // fremde Links im Browser
            }

            @Override
            public void onReceivedSslError(WebView v, SslErrorHandler h, SslError e) {
                if (prefs.getBoolean("insecure", false)) h.proceed(); else h.cancel();
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = cb;
                try {
                    startActivityForResult(p.createIntent(), REQ_FILE);
                } catch (ActivityNotFoundException e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }
        });
        startFlow();
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req == REQ_FILE && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res, data));
            fileCallback = null;
            return;
        }
        super.onActivityResult(req, res, data);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!session.isEmpty() && System.currentTimeMillis() - sessionAt > REFRESH_MS) refreshSession(false);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack(); else super.onBackPressed();
    }

    void startFlow() {
        if (prefs.getString("server", "").isEmpty() || prefs.getString("device_token", "").isEmpty()) {
            showLogin("");
            return;
        }
        refreshSession(true);
    }

    void showLogin(String message) {
        loginMessage = message == null ? "" : message;
        session = "";
        invalidateOptionsMenu();
        web.loadUrl("file:///android_asset/login.html");
    }

    void showDashboard(String solo, String clientId) {
        String server = prefs.getString("server", "");
        String q = "";
        try {
            if ("console".equals(solo)) q = "?solo=console&via=client";      // RMM-Konsole, ohne Geraet
            else if (solo != null) q = "?solo=" + URLEncoder.encode(solo, "UTF-8") + "&client=" + URLEncoder.encode(clientId, "UTF-8");
        } catch (Exception ignored) { }
        web.loadUrl(server + "/" + q + "#client-token=" + session);
        invalidateOptionsMenu();
    }

    /** Geraetetoken gegen eine frische Sitzung tauschen. */
    void refreshSession(final boolean thenShowDashboard) {
        new Thread(new Runnable() {
            public void run() {
                try {
                    JSONObject body = new JSONObject();
                    body.put("device_token", prefs.getString("device_token", ""));
                    body.put("app_version", VERSION);
                    JSONObject r = new JSONObject(http("POST", "/api/client/session", body.toString(), false));
                    session = r.getString("access_token");
                    sessionAt = System.currentTimeMillis();
                    ui.post(new Runnable() { public void run() {
                        if (prefs.getBoolean("auto_update", true)
                                && System.currentTimeMillis() - prefs.getLong("update_checked", 0) > REFRESH_MS) checkUpdate(false);
                        if (thenShowDashboard) showDashboard(null, null);
                        else web.evaluateJavascript("localStorage.setItem('rmm_token'," + JSONObject.quote(session) + ")", null);
                    }});
                } catch (final HttpError e) {
                    ui.post(new Runnable() { public void run() {
                        if (e.status == 401) {
                            prefs.edit().remove("device_token").remove("device_id").apply();
                            showLogin("Bitte neu anmelden: " + e.getMessage());
                        } else showLogin("Server nicht erreichbar: " + e.getMessage());
                    }});
                } catch (final Exception e) {
                    ui.post(new Runnable() { public void run() { showLogin("Server nicht erreichbar: " + e.getMessage()); }});
                }
            }
        }).start();
    }

    // ================================================================ JavaScript-Bruecke (Anmeldeseite)
    class Bridge {
        /** Dashboard (Modul "client"): Startschein einloesen und RDP/VNC/SSH extern oeffnen
         *  bzw. das RMM-Terminal anzeigen. Der Schein gehoert zum angemeldeten Benutzer. */
        @JavascriptInterface
        public void launch(final String ticket) {
            new Thread(new Runnable() { public void run() {
                try {
                    final JSONObject p = new JSONObject(http("POST", "/api/client/launch/" + URLEncoder.encode(ticket, "UTF-8"), "{}", true));
                    final String proto = p.optString("protocol");
                    if (proto.equals("shell")) {
                        ui.post(new Runnable() { public void run() { showDashboard("terminal", p.optString("client_id")); }});
                        return;
                    }
                    if (p.optString("host_mode").equals("tunnel")) {
                        ui.post(new Runnable() { public void run() { message("Fernzugriff",
                            "„Über RMM (Tunnel)“ geht nur in der Desktop-App (Windows/Linux). Bitte lokale IP, eigene Adresse oder das RMM-Gateway wählen."); }});
                        return;
                    }
                    final JSONObject fw = new JSONObject(http("POST", "/api/client/connect", p.toString(), true));
                    ui.post(new Runnable() { public void run() {
                        openRemote(proto, fw.optString("host"), fw.optInt("port"), fw.optString("user", p.optString("user")));
                    }});
                } catch (final Exception e) {
                    ui.post(new Runnable() { public void run() { message("Fernzugriff", e.getMessage()); }});
                }
            }}).start();
        }

        /** VPN (Modul "vpn"): eigenen Tunnel an die WireGuard-App uebergeben.
         *  Die Datei kommt ueber einen eigenen ContentProvider (Name = Tunnelname.conf);
         *  Android zeigt die Auswahl "Oeffnen mit" -> WireGuard -> "Importieren". */
        @JavascriptInterface
        public void wgImport(final String tunnelId) {
            new Thread(new Runnable() { public void run() {
                try {
                    JSONObject my = new JSONObject(http("GET", "/api/vpn/my", null, true));
                    org.json.JSONArray arr = my.optJSONArray("tunnels");
                    JSONObject t = null;
                    for (int i = 0; arr != null && i < arr.length(); i++) {
                        if (arr.getJSONObject(i).optString("tunnel").equals(tunnelId)) { t = arr.getJSONObject(i); break; }
                    }
                    if (t == null) throw new Exception("Der VPN-Tunnel wurde nicht gefunden oder ist abgelaufen.");
                    final String name = t.optString("iface");
                    java.io.File dir = new java.io.File(getCacheDir(), "wg");
                    dir.mkdirs();
                    java.io.File f = new java.io.File(dir, name + ".conf");
                    java.io.FileOutputStream out = new java.io.FileOutputStream(f);
                    out.write(t.optString("conf").getBytes("UTF-8"));
                    out.close();
                    ui.post(new Runnable() { public void run() { openWireGuard(name); }});
                } catch (final Exception e) {
                    ui.post(new Runnable() { public void run() { message("VPN", e.getMessage()); }});
                }
            }}).start();
        }

        /** Tunnel in der WireGuard-App ein-/ausschalten (WireGuard-Einstellung
         *  "Fernsteuerung durch Apps erlauben" muss einmal aktiviert sein). */
        @JavascriptInterface
        public void wgSet(final String name, final boolean up) {
            ui.post(new Runnable() { public void run() {
                Intent i = new Intent(up ? "com.wireguard.android.action.SET_TUNNEL_UP" : "com.wireguard.android.action.SET_TUNNEL_DOWN");
                i.setPackage("com.wireguard.android");
                i.putExtra("tunnel", name);
                sendBroadcast(i);
                android.widget.Toast.makeText(MainActivity.this, up ? "WireGuard: " + name + " wird verbunden …"
                    : "WireGuard: " + name + " wird getrennt …", android.widget.Toast.LENGTH_LONG).show();
            }});
        }

        @JavascriptInterface
        public boolean wgInstalled() {
            try { getPackageManager().getPackageInfo("com.wireguard.android", 0); return true; }
            catch (Exception e) { return false; }
        }

        @JavascriptInterface
        public String prefill() {
            try {
                JSONObject o = new JSONObject();
                o.put("server", prefs.getString("server", ""));
                o.put("username", prefs.getString("username", ""));
                o.put("device_name", prefs.getString("device_name", deviceName()));
                o.put("insecure", prefs.getBoolean("insecure", false));
                o.put("message", loginMessage);
                return o.toString();
            } catch (Exception e) { return "{}"; }
        }

        @JavascriptInterface
        public String realms(String server, boolean insecure) {
            try {
                prefs.edit().putString("server", normalize(server)).putBoolean("insecure", insecure).apply();
                return http("GET", "/api/auth/realms", null, false);
            } catch (Exception e) { return "[]"; }
        }

        @JavascriptInterface
        public String login(String server, String user, String pass, String realm, String code, String name, boolean insecure) {
            JSONObject out = new JSONObject();
            try {
                prefs.edit().putString("server", normalize(server)).putBoolean("insecure", insecure).apply();
                JSONObject b = new JSONObject();
                b.put("username", user); b.put("password", pass); b.put("realm", realm == null || realm.isEmpty() ? "local" : realm);
                b.put("code", code == null ? "" : code);
                JSONObject lr = new JSONObject(http("POST", "/api/auth/login", b.toString(), false));
                session = lr.getString("token");
                JSONObject d = new JSONObject();
                d.put("name", name == null || name.isEmpty() ? deviceName() : name);
                d.put("platform", "android");
                d.put("os", "Android " + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")");
                d.put("app_version", VERSION);
                JSONObject reg = new JSONObject(http("POST", "/api/client/register", d.toString(), true));
                session = reg.getString("access_token");
                sessionAt = System.currentTimeMillis();
                prefs.edit().putString("username", user).putString("device_name", d.getString("name"))
                        .putString("device_token", reg.getString("device_token"))
                        .putString("device_id", reg.getJSONObject("device").getString("id")).apply();
                ui.post(new Runnable() { public void run() { showDashboard(null, null); }});
                out.put("ok", true);
            } catch (HttpError e) {
                try { out.put("ok", false); out.put("error", e.getMessage()); } catch (Exception ignored) { }
            } catch (Exception e) {
                try { out.put("ok", false); out.put("error", "Server nicht erreichbar: " + e.getMessage()); } catch (Exception ignored) { }
            }
            return out.toString();
        }
    }

    // ================================================================ Menue
    static final int M_HOME = 1, M_SHELL = 2, M_RDP = 3, M_VNC = 4, M_RELAY = 5, M_LOGOUT = 6, M_UPDATE = 7, M_AUTOUPD = 8, M_CONSOLE = 9;

    @Override
    public boolean onCreateOptionsMenu(Menu m) {
        m.clear();
        if (session.isEmpty()) return true;
        m.add(0, M_HOME, 0, "Dashboard");
        m.add(0, M_SHELL, 1, "Remote-Shell …");
        m.add(0, M_CONSOLE, 1, "RMM-Konsole");
        m.add(0, M_RDP, 2, "Fernbild (Remotedesktop) …");
        m.add(0, M_VNC, 3, "Fernbild (VNC) …");
        m.add(0, M_RELAY, 4, "Relay-Zugang");
        m.add(0, M_UPDATE, 5, "Nach Updates suchen (" + VERSION + ")");
        m.add(0, M_AUTOUPD, 6, "Automatisch nach Updates suchen").setCheckable(true)
                .setChecked(prefs.getBoolean("auto_update", true));
        m.add(0, M_LOGOUT, 7, "Dieses Gerät abmelden");
        return true;
    }

    @Override
    public boolean onPrepareOptionsMenu(Menu m) { return onCreateOptionsMenu(m); }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        switch (item.getItemId()) {
            case M_HOME: showDashboard(null, null); return true;
            case M_CONSOLE: showDashboard("console", ""); return true;
            case M_SHELL: chooseClient("Remote-Shell", true, new OnClient() { public void chosen(JSONObject c) {
                showDashboard("terminal", c.optString("id")); }}); return true;
            // Fernbild: auch Offline-Agenten - direkte Verbindung (lokale IP / VPN) braucht keinen Agenten
            case M_RDP: chooseClient("Fernbild per Remotedesktop", false, new OnClient() { public void chosen(JSONObject c) {
                screen(c, "rdp"); }}); return true;
            case M_VNC: chooseClient("Fernbild per VNC", false, new OnClient() { public void chosen(JSONObject c) {
                screen(c, "vnc"); }}); return true;
            case M_RELAY: relayInfo(); return true;
            case M_LOGOUT: logout(); return true;
            case M_UPDATE: checkUpdate(true); return true;
            case M_AUTOUPD:
                boolean on = !prefs.getBoolean("auto_update", true);
                prefs.edit().putBoolean("auto_update", on).apply();
                item.setChecked(on);
                return true;
        }
        return super.onOptionsItemSelected(item);
    }

    interface OnClient { void chosen(JSONObject c); }

    void chooseClient(final String title, final boolean onlineOnly, final OnClient cb) {
        new Thread(new Runnable() { public void run() {
            try {
                JSONArray all = new JSONArray(http("GET", "/api/clients", null, true));
                final List<JSONObject> online = new ArrayList<JSONObject>();
                for (int i = 0; i < all.length(); i++)
                    if (!onlineOnly || all.getJSONObject(i).optBoolean("online")) online.add(all.getJSONObject(i));
                Collections.sort(online, new Comparator<JSONObject>() { public int compare(JSONObject a, JSONObject b) {
                    boolean oa = a.optBoolean("online"), ob = b.optBoolean("online");
                    if (oa != ob) return oa ? -1 : 1;
                    return a.optString("hostname").compareToIgnoreCase(b.optString("hostname")); }});
                ui.post(new Runnable() { public void run() {
                    if (online.isEmpty()) { message(title, onlineOnly ? "Kein Gerät online." : "Keine Geräte sichtbar."); return; }
                    String[] labels = new String[online.size()];
                    for (int i = 0; i < labels.length; i++)
                        labels[i] = (online.get(i).optBoolean("online") ? "● " : "○ ") + online.get(i).optString("hostname")
                                + "  (" + online.get(i).optString("platform", "?") + ")"
                                + (online.get(i).optBoolean("online") ? "" : " – offline");
                    new AlertDialog.Builder(MainActivity.this).setTitle(title + " – Gerät wählen")
                            .setItems(labels, new DialogInterface.OnClickListener() { public void onClick(DialogInterface d, int w) {
                                cb.chosen(online.get(w)); }})
                            .setNegativeButton("Abbrechen", null).show();
                }});
            } catch (final Exception e) {
                ui.post(new Runnable() { public void run() { message(title, e.getMessage()); }});
            }
        }}).start();
    }

    // Fernbild: Ziel + Port waehlen (RMM-Gateway, lokale IP, letzte, eigene), Voreinstellung pro Benutzer+Geraet
    void screen(final JSONObject client, final String protocol) {
        new Thread(new Runnable() { public void run() {
            try {
                final JSONObject info = new JSONObject(http("GET", "/api/client/targets/"
                        + URLEncoder.encode(client.optString("id"), "UTF-8") + "?protocol=" + protocol, null, true));
                ui.post(new Runnable() { public void run() { connectDialog(client, protocol, info); }});
            } catch (final Exception e) {
                ui.post(new Runnable() { public void run() { message("Fernbild", e.getMessage()); }});
            }
        }}).start();
    }

    int dp(int v) { return (int) (v * getResources().getDisplayMetrics().density + 0.5f); }

    void connectDialog(final JSONObject client, final String protocol, final JSONObject info) {
        final int std = info.optInt("standard_port", protocol.equals("rdp") ? 3389 : 5900);
        final JSONObject def = info.optJSONObject("default") == null ? new JSONObject() : info.optJSONObject("default");
        final JSONObject last = info.optJSONObject("last");
        final String localIp = info.optString("local_ip", "");
        final boolean gw = info.optBoolean("gateway_available");

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(8), dp(20), 0);
        TextView h1 = new TextView(this); h1.setText("Ziel"); box.addView(h1);
        final RadioGroup hosts = new RadioGroup(this);
        final RadioButton rGw = new RadioButton(this); rGw.setText("RMM-Gateway (nur von diesem Gerät erreichbar)"); rGw.setEnabled(gw);
        final RadioButton rLocal = new RadioButton(this); rLocal.setText("Lokale IP: " + (localIp.isEmpty() ? "nicht bekannt" : localIp));
        rLocal.setEnabled(!localIp.isEmpty());
        final RadioButton rLast = new RadioButton(this);
        final RadioButton rCustom = new RadioButton(this); rCustom.setText("Eigene Adresse");
        rGw.setId(1); rLocal.setId(2); rLast.setId(3); rCustom.setId(4);
        hosts.addView(rGw); hosts.addView(rLocal);
        if (last != null) { rLast.setText("Letzte Verbindung: " + last.optString("display")); hosts.addView(rLast); }
        hosts.addView(rCustom);
        box.addView(hosts);
        final EditText eHost = new EditText(this);
        eHost.setHint("z. B. 10.8.0.25 oder pc.firma.lan");
        eHost.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        eHost.setText(def.optString("host", ""));
        box.addView(eHost);

        TextView h2 = new TextView(this); h2.setText("Port"); h2.setPadding(0, dp(10), 0, 0); box.addView(h2);
        final RadioGroup ports = new RadioGroup(this);
        ports.setOrientation(RadioGroup.HORIZONTAL);
        final RadioButton pStd = new RadioButton(this); pStd.setText("Standard (" + std + ")"); pStd.setId(11);
        final RadioButton pGw = new RadioButton(this); pGw.setText("Gateway"); pGw.setId(12); pGw.setEnabled(false);
        final RadioButton pCu = new RadioButton(this); pCu.setText("Eigener"); pCu.setId(13);
        ports.addView(pStd); ports.addView(pGw); ports.addView(pCu);
        box.addView(ports);
        final EditText ePort = new EditText(this);
        ePort.setHint("Port"); ePort.setInputType(InputType.TYPE_CLASS_NUMBER);
        if (def.optInt("port", 0) > 0) ePort.setText(String.valueOf(def.optInt("port")));
        box.addView(ePort);
        final CheckBox remember = new CheckBox(this); remember.setText("Als Standard für dieses Gerät merken");
        box.addView(remember);

        // Vorbelegung: gespeicherte Voreinstellung, sonst lokale IP (bzw. Gateway, wenn keine IP bekannt)
        String hm = def.optString("host_mode", localIp.isEmpty() ? "gateway" : "local");
        if (hm.equals("gateway") && !gw) hm = localIp.isEmpty() ? "custom" : "local";
        if (hm.equals("local") && localIp.isEmpty()) hm = gw ? "gateway" : "custom";
        hosts.check(hm.equals("gateway") ? 1 : hm.equals("custom") ? 4 : 2);
        ports.check(def.optString("port_mode", "standard").equals("custom") ? 13 : 11);
        final Runnable sync = new Runnable() { public void run() {
            int h = hosts.getCheckedRadioButtonId();
            boolean fixed = h == 1 || h == 3;
            if (h == 1) ports.check(12); else if (ports.getCheckedRadioButtonId() == 12) ports.check(11);
            pStd.setEnabled(!fixed); pCu.setEnabled(!fixed); ePort.setEnabled(!fixed);
            eHost.setEnabled(h == 4);
        }};
        hosts.setOnCheckedChangeListener(new RadioGroup.OnCheckedChangeListener() {
            public void onCheckedChanged(RadioGroup g, int id) { sync.run(); }});
        sync.run();

        ScrollView sv = new ScrollView(this);
        sv.addView(box);
        new AlertDialog.Builder(this)
                .setTitle((protocol.equals("rdp") ? "Remotedesktop: " : "VNC: ") + client.optString("hostname"))
                .setView(sv)
                .setNegativeButton("Abbrechen", null)
                .setPositiveButton("Verbinden", new DialogInterface.OnClickListener() { public void onClick(DialogInterface d, int w) {
                    int h = hosts.getCheckedRadioButtonId();
                    String hostMode = h == 1 ? "gateway" : h == 3 ? "last" : h == 4 ? "custom" : "local";
                    String portMode = ports.getCheckedRadioButtonId() == 13 ? "custom" : "standard";
                    doConnect(client, protocol, hostMode, eHost.getText().toString().trim(), portMode,
                              ePort.getText().toString().trim(), remember.isChecked());
                }})
                .show();
    }

    /** RDP / VNC / SSH in der passenden App oeffnen (Intent mit URI). */
    void openRemote(String protocol, String h, int port, String user) {
        String uri = protocol.equals("rdp") ? "rdp://full%20address=s:" + h + ":" + port
                : protocol.equals("ssh") ? "ssh://" + (user == null || user.isEmpty() ? "" : Uri.encode(user) + "@") + h + ":" + port
                : "vnc://" + h + ":" + port;
        try {
            Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(uri));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (ActivityNotFoundException e) {
            String app = protocol.equals("rdp") ? "„Remote Desktop“ (Microsoft) bzw. „Windows App“"
                    : protocol.equals("ssh") ? "einen SSH-Client (z. B. „Termius“ oder „JuiceSSH“)" : "„bVNC“";
            message("Fernzugriff", "Keine passende App gefunden. Bitte " + app + " aus dem Play Store installieren.\n\n"
                    + "Verbindung: " + h + ":" + port);
        }
    }

    void doConnect(final JSONObject client, final String protocol, final String hostMode, final String host,
                   final String portMode, final String portText, final boolean remember) {
        new Thread(new Runnable() { public void run() {
            try {
                JSONObject b = new JSONObject();
                b.put("client_id", client.optString("id")); b.put("protocol", protocol);
                b.put("host_mode", hostMode); b.put("host", host); b.put("port_mode", portMode);
                if (portMode.equals("custom")) {
                    try { b.put("port", Integer.parseInt(portText)); } catch (NumberFormatException e) { b.put("port", 0); }
                }
                b.put("remember", remember);
                final JSONObject fw = new JSONObject(http("POST", "/api/client/connect", b.toString(), true));
                ui.post(new Runnable() { public void run() {
                    openRemote(protocol, fw.optString("host"), fw.optInt("port"), "");
                }});
            } catch (final Exception e) {
                ui.post(new Runnable() { public void run() { message("Fernbild", e.getMessage()); }});
            }
        }}).start();
    }

    // ================================================================ Updates
    static int[] vt(String v) {
        String[] p = (v == null ? "0" : v).split("\\.");
        int[] out = new int[3];
        for (int i = 0; i < 3 && i < p.length; i++) { try { out[i] = Integer.parseInt(p[i].trim()); } catch (Exception e) { out[i] = 0; } }
        return out;
    }

    static boolean newer(String a, String b) {          // a > b ?
        int[] x = vt(a), y = vt(b);
        for (int i = 0; i < 3; i++) if (x[i] != y[i]) return x[i] > y[i];
        return false;
    }

    /** Neueste APK auf dem Server pruefen; bei neuerer Version anbieten (Download ueber den Browser). */
    void checkUpdate(final boolean manual) {
        new Thread(new Runnable() { public void run() {
            try {
                JSONObject info = new JSONObject(http("GET", "/client-dist/info", null, false));
                JSONArray files = info.optJSONArray("files");
                JSONObject best = null;
                for (int i = 0; files != null && i < files.length(); i++) {
                    JSONObject f = files.getJSONObject(i);
                    if (!"android".equals(f.optString("platform")) || !".apk".equals(f.optString("ext"))) continue;
                    if (best == null || newer(f.optString("version"), best.optString("version"))) best = f;
                }
                prefs.edit().putLong("update_checked", System.currentTimeMillis()).apply();
                final JSONObject apk = best;
                ui.post(new Runnable() { public void run() {
                    if (apk == null || !newer(apk.optString("version"), VERSION)) {
                        if (manual) message("Update", apk == null ? "Auf dem Server liegt keine Android-App."
                                : "Die App ist aktuell (Version " + VERSION + ").");
                        return;
                    }
                    final String url = prefs.getString("server", "") + apk.optString("url");
                    new AlertDialog.Builder(MainActivity.this).setTitle("Update verfügbar")
                            .setMessage("Version " + apk.optString("version") + " ist verfügbar (installiert: " + VERSION + ").\n\n"
                                    + "Die neue App wird heruntergeladen; danach die Datei öffnen und „Aktualisieren“ wählen.")
                            .setPositiveButton("Herunterladen", new DialogInterface.OnClickListener() { public void onClick(DialogInterface d, int w) {
                                try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url))); }
                                catch (Exception e) { message("Update", "Download konnte nicht gestartet werden: " + url); }
                            }})
                            .setNegativeButton("Später", null).show();
                }});
            } catch (final Exception e) {
                if (manual) ui.post(new Runnable() { public void run() { message("Update", "Suche fehlgeschlagen: " + e.getMessage()); }});
            }
        }}).start();
    }

    void relayInfo() {
        message("Relay-Zugang",
                "Android erlaubt Apps keine Netzlaufwerke. Nutze den Explorer im Dashboard (Relay bzw. ☁️ Spaces) "
                + "oder eine Datei-App mit WebDAV (z. B. „Material Files“):\n\n"
                + prefs.getString("server", "") + "/dav/\nBenutzer: " + prefs.getString("username", "")
                + "\nPasswort: dein normales Passwort");
    }

    void logout() {
        new AlertDialog.Builder(this).setTitle("Abmelden")
                .setMessage("Dieses Gerät abmelden? Die gespeicherte Anmeldung wird gelöscht.")
                .setPositiveButton("Abmelden", new DialogInterface.OnClickListener() { public void onClick(DialogInterface d, int w) {
                    final String id = prefs.getString("device_id", "");
                    new Thread(new Runnable() { public void run() {
                        try { if (!id.isEmpty()) http("DELETE", "/api/client/devices/" + URLEncoder.encode(id, "UTF-8"), null, true); }
                        catch (Exception ignored) { }
                        ui.post(new Runnable() { public void run() {
                            prefs.edit().remove("device_token").remove("device_id").apply();
                            web.evaluateJavascript("localStorage.removeItem('rmm_token')", null);
                            showLogin("Abgemeldet.");
                        }});
                    }}).start();
                }})
                .setNegativeButton("Abbrechen", null).show();
    }

    void message(String title, String msg) {
        new AlertDialog.Builder(this).setTitle(title).setMessage(msg).setPositiveButton("OK", null).show();
    }

    // ================================================================ HTTP
    static class HttpError extends Exception {
        final int status;
        HttpError(String m, int s) { super(m); status = s; }
    }

    static String normalize(String s) {
        s = s == null ? "" : s.trim();
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        if (!s.isEmpty() && !s.contains("://")) s = "https://" + s;
        return s;
    }

    String deviceName() {
        String m = (Build.MANUFACTURER + " " + Build.MODEL).trim();
        return m.isEmpty() ? "Android-Gerät" : Character.toUpperCase(m.charAt(0)) + m.substring(1);
    }

    String http(String method, String path, String body, boolean auth) throws Exception {
        String server = prefs.getString("server", "");
        if (server.isEmpty()) throw new HttpError("Bitte die Server-Adresse eingeben", 0);
        HttpURLConnection c = (HttpURLConnection) new URL(server + path).openConnection();
        if (c instanceof HttpsURLConnection && prefs.getBoolean("insecure", false)) trustAll((HttpsURLConnection) c);
        c.setRequestMethod(method);
        c.setConnectTimeout(15000);
        c.setReadTimeout(30000);
        c.setRequestProperty("Accept", "application/json");
        c.setRequestProperty("User-Agent", "RapalleClient-Android/" + VERSION);
        if (auth && !session.isEmpty()) c.setRequestProperty("Authorization", "Bearer " + session);
        if (body != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            OutputStream os = c.getOutputStream();
            os.write(body.getBytes("UTF-8"));
            os.close();
        }
        int code = c.getResponseCode();
        InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        String text = "";
        if (in != null) {
            ByteArrayOutputStream bo = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) bo.write(buf, 0, n);
            in.close();
            text = bo.toString("UTF-8");
        }
        if (code >= 400) {
            String detail = "HTTP " + code;
            try { detail = new JSONObject(text).optString("detail", detail); } catch (Exception ignored) { }
            throw new HttpError(detail, code);
        }
        return text.isEmpty() ? "null" : text;
    }

    static void trustAll(HttpsURLConnection c) throws Exception {
        TrustManager[] tm = new TrustManager[]{ new X509TrustManager() {
            public void checkClientTrusted(X509Certificate[] a, String b) { }
            public void checkServerTrusted(X509Certificate[] a, String b) { }
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        }};
        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(null, tm, new java.security.SecureRandom());
        c.setSSLSocketFactory(ctx.getSocketFactory());
        c.setHostnameVerifier(new HostnameVerifier() { public boolean verify(String h, SSLSession s) { return true; } });
    }

    /** Konfiguration an WireGuard geben: zuerst "Oeffnen mit" (WireGuard importiert .conf-Dateien),
     *  sonst "Teilen". Fehlt WireGuard, in den Play Store. */
    void openWireGuard(String name) {
        boolean wg = false;
        try { getPackageManager().getPackageInfo("com.wireguard.android", 0); wg = true; } catch (Exception e) { /* fehlt */ }
        if (!wg) {
            message("VPN", "Bitte zuerst die App „WireGuard“ installieren, dann erneut tippen.");
            try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=com.wireguard.android"))); }
            catch (Exception e) { /* kein Store */ }
            return;
        }
        Uri uri = Uri.parse("content://net.rapalle.rmmclient.conf/" + name + ".conf");
        Intent view = new Intent(Intent.ACTION_VIEW);
        view.setDataAndType(uri, "application/x-wireguard-config");
        view.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        view.setPackage("com.wireguard.android");
        try {
            startActivity(view);
            return;
        } catch (ActivityNotFoundException e) { /* naechster Weg */ }
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType("application/octet-stream");
        send.putExtra(Intent.EXTRA_STREAM, uri);
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        try {
            startActivity(Intent.createChooser(send, "Tunnel „" + name + "“ an WireGuard übergeben"));
            android.widget.Toast.makeText(this, "In WireGuard „Importieren“ wählen; danach im RMM „Verbinden“.",
                android.widget.Toast.LENGTH_LONG).show();
        } catch (Exception e) {
            message("VPN", "WireGuard konnte die Datei nicht übernehmen. Alternativ im Dashboard den QR-Code mit WireGuard scannen.");
        }
    }

    /** Liefert die Tunnel-Dateien aus dem Cache an WireGuard (nur lesend, nur per Freigabe). */
    public static class ConfProvider extends android.content.ContentProvider {
        @Override public boolean onCreate() { return true; }
        private java.io.File file(Uri uri) {
            String n = uri.getLastPathSegment();
            if (n == null || n.contains("/") || n.contains("..")) return null;
            return new java.io.File(new java.io.File(getContext().getCacheDir(), "wg"), n);
        }
        @Override public android.database.Cursor query(Uri uri, String[] proj, String sel, String[] args, String sort) {
            java.io.File f = file(uri);
            android.database.MatrixCursor c = new android.database.MatrixCursor(
                new String[] {android.provider.OpenableColumns.DISPLAY_NAME, android.provider.OpenableColumns.SIZE});
            if (f != null && f.exists()) c.addRow(new Object[] {f.getName(), f.length()});
            return c;
        }
        @Override public String getType(Uri uri) { return "application/x-wireguard-config"; }
        @Override public android.os.ParcelFileDescriptor openFile(Uri uri, String mode) throws java.io.FileNotFoundException {
            java.io.File f = file(uri);
            if (f == null || !f.exists()) throw new java.io.FileNotFoundException();
            return android.os.ParcelFileDescriptor.open(f, android.os.ParcelFileDescriptor.MODE_READ_ONLY);
        }
        @Override public Uri insert(Uri uri, android.content.ContentValues v) { return null; }
        @Override public int delete(Uri uri, String s, String[] a) { return 0; }
        @Override public int update(Uri uri, android.content.ContentValues v, String s, String[] a) { return 0; }
    }
}
