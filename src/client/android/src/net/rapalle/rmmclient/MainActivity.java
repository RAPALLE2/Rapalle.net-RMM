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
import android.view.Menu;
import android.view.MenuItem;
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
            if (solo != null) q = "?solo=" + URLEncoder.encode(solo, "UTF-8") + "&client=" + URLEncoder.encode(clientId, "UTF-8");
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
    static final int M_HOME = 1, M_SHELL = 2, M_RDP = 3, M_VNC = 4, M_RELAY = 5, M_LOGOUT = 6;

    @Override
    public boolean onCreateOptionsMenu(Menu m) {
        m.clear();
        if (session.isEmpty()) return true;
        m.add(0, M_HOME, 0, "Dashboard");
        m.add(0, M_SHELL, 1, "Remote-Shell …");
        m.add(0, M_RDP, 2, "Fernbild (Remotedesktop) …");
        m.add(0, M_VNC, 3, "Fernbild (VNC) …");
        m.add(0, M_RELAY, 4, "Relay-Zugang");
        m.add(0, M_LOGOUT, 5, "Dieses Gerät abmelden");
        return true;
    }

    @Override
    public boolean onPrepareOptionsMenu(Menu m) { return onCreateOptionsMenu(m); }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        switch (item.getItemId()) {
            case M_HOME: showDashboard(null, null); return true;
            case M_SHELL: chooseClient("Remote-Shell", new OnClient() { public void chosen(JSONObject c) {
                showDashboard("terminal", c.optString("id")); }}); return true;
            case M_RDP: chooseClient("Fernbild per Remotedesktop", new OnClient() { public void chosen(JSONObject c) {
                screen(c, "rdp"); }}); return true;
            case M_VNC: chooseClient("Fernbild per VNC", new OnClient() { public void chosen(JSONObject c) {
                screen(c, "vnc"); }}); return true;
            case M_RELAY: relayInfo(); return true;
            case M_LOGOUT: logout(); return true;
        }
        return super.onOptionsItemSelected(item);
    }

    interface OnClient { void chosen(JSONObject c); }

    void chooseClient(final String title, final OnClient cb) {
        new Thread(new Runnable() { public void run() {
            try {
                JSONArray all = new JSONArray(http("GET", "/api/clients", null, true));
                final List<JSONObject> online = new ArrayList<JSONObject>();
                for (int i = 0; i < all.length(); i++) if (all.getJSONObject(i).optBoolean("online")) online.add(all.getJSONObject(i));
                Collections.sort(online, new Comparator<JSONObject>() { public int compare(JSONObject a, JSONObject b) {
                    return a.optString("hostname").compareToIgnoreCase(b.optString("hostname")); }});
                ui.post(new Runnable() { public void run() {
                    if (online.isEmpty()) { message(title, "Kein Gerät online."); return; }
                    String[] labels = new String[online.size()];
                    for (int i = 0; i < labels.length; i++)
                        labels[i] = online.get(i).optString("hostname") + "  (" + online.get(i).optString("platform", "?") + ")";
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

    void screen(final JSONObject client, final String protocol) {
        new Thread(new Runnable() { public void run() {
            try {
                JSONObject b = new JSONObject();
                b.put("client_id", client.optString("id")); b.put("protocol", protocol);
                final JSONObject fw = new JSONObject(http("POST", "/api/client/screen", b.toString(), true));
                ui.post(new Runnable() { public void run() {
                    String host = fw.optString("host"); int port = fw.optInt("port");
                    String uri = protocol.equals("rdp")
                            ? "rdp://full%20address=s:" + host + ":" + port
                            : "vnc://" + host + ":" + port;
                    try {
                        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse(uri));
                        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        startActivity(i);
                    } catch (ActivityNotFoundException e) {
                        String app = protocol.equals("rdp") ? "„Remote Desktop“ (Microsoft) bzw. „Windows App“" : "„bVNC“";
                        message("Fernbild", "Keine passende App gefunden. Bitte " + app + " aus dem Play Store installieren.\n\n"
                                + "Verbindung: " + host + ":" + port + " (nur von diesem Gerät erreichbar)");
                    }
                }});
            } catch (final Exception e) {
                ui.post(new Runnable() { public void run() { message("Fernbild", e.getMessage()); }});
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
}
