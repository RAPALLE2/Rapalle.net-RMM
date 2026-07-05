// i18n.js
// -------
// Einfaches Übersetzungssystem. Der Rest des Codes ruft t("schlüssel") auf und
// bekommt den Text in der aktuell eingestellten Sprache zurück.
//
// Sprache wechseln: setLanguage("en") oder setLanguage("de").
// Die aktuelle Sprache wird auch im localStorage gemerkt, damit sie einen
// Seiten-Neuladen übersteht.

const translations = {
  de: {
    // Allgemein / Login
    login: "Anmelden", username: "Benutzername", password: "Passwort",
    logout: "Abmelden", settings: "Einstellungen", profile: "Profil",
    save: "Speichern", cancel: "Abbrechen", delete: "Löschen", add: "Hinzufügen",
    // Sidebar / Hierarchie
    devices: "Geräte", add_client: "+ Client hinzufügen", not_assigned: "Nicht zugeordnet",
    select_hint: "Wähle links einen Tenant, Standort oder Client aus.",
    no_devices_sidebar: "Noch keine Geräte. Füge unten einen Client hinzu.",
    manage_hierarchy: "Tenants & Standorte verwalten",
    // Status-Panel
    status: "Status", status_online: "Online", status_offline: "Offline",
    status_maintenance: "Wartung", status_crashed: "Abgestürzt",
    os: "Betriebssystem", ip: "IP-Adresse", hostname: "Hostname", arch: "Architektur",
    uptime: "Laufzeit", reboot: "Neustart", shutdown: "Herunterfahren", edit: "Bearbeiten",
    update_agent: "Agent aktualisieren",
    // Übersicht
    overview: "Übersicht", tab_metrics: "Metrics", tab_notes: "Notes", tab_disk: "Disk",
    notes_placeholder: "Notizen zu diesem Client...",
    no_live_data: "Keine Live-Daten verfügbar (Client offline).",
    cores: "Kerne", threads: "Threads", free: "Frei",
    network: "Netzwerk", time_range: "Zeitspanne",
    cpu_history: "CPU-Verlauf", ram_history: "RAM-Verlauf", network_history: "Netzwerk-Verlauf",
    // Actions
    actions: "Aktionen", file_explorer: "Datei-Explorer", terminal: "Terminal",
    remote_screen: "Remote Screen", task_manager: "Task-Manager",
    command_sent: "Befehl wurde gesendet.", client_not_found: "Client nicht gefunden.",
    // Aggregierte Ansicht
    online: "online", no_devices: "Keine Geräte in dieser Auswahl.",
    no_location: "Ohne Standort",
    // Startmenü / App-Namen
    menu_apps: "Anwendungen", device_overview: "Geräte-Übersicht",
    tenants_locations: "Tenants & Standorte", scripts: "Scripts",
    bulk_shell: "Bulk Remote Shell", network_scanner: "Netzwerk-Scanner",
    recordings: "Aufzeichnungen", tower_defense: "Tower Defense",
    audit_log: "Audit-Log", automation: "Automation", notifications: "Benachrichtigungen",
    // Allgemeine Buttons/Begriffe
    refresh: "Aktualisieren", run: "Ausführen", close: "Schließen",
    name: "Name", command: "Befehl", type: "Typ", actions_col: "Aktionen",
    created: "Erstellt", enabled: "Aktiviert", disabled: "Deaktiviert",
    send: "Senden", test: "Testen", generate: "Erzeugen",
    // Settings-Tabs
    tab_general: "Allgemein", tab_users: "Benutzer", tab_sso: "SSO / Verzeichnis",
    tab_notifications: "Benachrichtigungen", tab_groups: "Gruppen & Rollen",
    // Allgemein-Tab
    general_loading: "Einstellungen werden geladen…",
    general_server_title: "Server-Adresse (Installation)",
    general_server_hint: "Diese Adresse wird in die Client-Install-Befehle eingebaut. Leer lassen = automatisch aus dem Aufruf ableiten (ergibt lokal oft „localhost\").",
    general_server_url: "Server-URL / IP",
    general_metrics_title: "Metrik-Historie",
    general_metrics_hint: "Legt fest, in welchem Abstand Graphen-Datenpunkte gespeichert werden, wie lange sie aufbewahrt werden und wie lange Replays erhalten bleiben.",
    general_metrics_interval: "Speicher-Intervall (Sekunden)",
    general_metrics_retention: "Aufbewahrung Graphen (Stunden)",
    general_replay_retention: "Aufbewahrung Replays (Tage)",
    general_saved: "Einstellungen gespeichert",
    // Portscan
    portscan: "Portscan",
    ps_ip_placeholder: "IP-Adresse (z.B. 192.168.1.10)",
    ps_mode_standard: "Standard-Ports", ps_mode_all: "Alle Ports (1–65535)", ps_mode_custom: "Eigene Ports",
    ps_scan: "Scannen", ps_custom_placeholder: "z.B. 22,80,8000-8100",
    ps_need_ip: "Bitte eine IP-Adresse eingeben.", ps_need_ports: "Bitte Ports angeben (z.B. 22,80,8000-8100).",
    ps_scanning: "Scanne Ports…", ps_scanning_all: "Scanne alle Ports (kann etwas dauern)…",
    ps_ports_checked: "Ports geprüft", ps_none: "Keine offenen Ports gefunden.",
    ps_open_found: "offene Ports gefunden", ps_open_ports: "Offene Ports",
    // Allgemein-Tab: Server-Adresse (erweitert)
    general_server_ip: "Server-IP / Host", general_server_domain: "Domain (optional)",
    general_backend_port: "Backend-Port", general_frontend_port: "Frontend-Port",
    general_server_url_advanced: "Vollständige URL (überschreibt IP/Domain/Port)",
    // Automation
    auto_new: "Neue Automation", auto_schedule: "Zeitplan", auto_target: "Ziel-Clients",
    auto_every: "Ausführen alle", minutes: "Minuten", hours: "Stunden", days: "Tage",
    auto_none: "Noch keine Automationen angelegt.",
    // Notifications
    notif_webhook_url: "Webhook-URL", notif_type: "Webhook-Typ",
    notif_discord: "Discord", notif_custom: "Benutzerdefiniert",
    notif_none: "Noch keine Webhooks konfiguriert.",
    notif_test_sent: "Test-Benachrichtigung gesendet.",
    // SSO / Realms
    sso_intro: "Verbinde ein externes Verzeichnis (z.B. Active Directory), um Anmeldungen zu delegieren.",
    sso_realm_name: "Realm-Name", sso_server: "Server (Host/IP)", sso_base_dn: "Base DN",
    sso_bind_user: "Bind-Benutzer (DN)", sso_bind_pw: "Bind-Passwort",
    sso_none: "Noch keine Verzeichnisse verbunden.",
    sso_add_realm: "Verzeichnis / Realm hinzufügen", sso_edit_realm: "Realm bearbeiten",
    sso_port: "Port (optional)", sso_use_ssl: "LDAPS (SSL)",
    sso_pw_unchanged: "(unverändert lassen)", sso_user_filter: "Zusätzlicher Filter (optional)",
    sso_filter_hint: "Optionaler LDAP-Filter, um nur bestimmte Benutzer zuzulassen (wird mit dem Standardfilter kombiniert).",
    sso_connected: "Verbundene Verzeichnisse", sso_enable: "Aktivieren", sso_disable: "Deaktivieren",
    sso_test_ok: "Verbindung erfolgreich – Konfiguration ist gültig.",
    sso_test_fail: "Test fehlgeschlagen:",
    sso_delete_confirm: "Verzeichnis wirklich löschen?",
    sso_group_hint: "AD-Benutzer erhalten ihre Rechte über RMM-Gruppen, die genauso heißen wie ihre AD-Gruppen. Lege dafür im Tab „Gruppen & Rollen\" Gruppen mit den passenden Namen an.",
    // Gruppen & Rollen
    groups_intro: "Gruppen bündeln Rechte. Weise Benutzern oder AD-Gruppen eine Rolle zu.",
    role: "Rolle", permissions: "Rechte", group_name: "Gruppen-/AD-Gruppenname",
    perm_login: "Anmelden", perm_screen: "Remote Screen", perm_terminal: "Terminal",
    perm_explorer: "Datei-Explorer", perm_quick: "Schnellaktionen (Neustart etc.)",
    perm_audit: "Audit-Log ansehen", perm_manage_users: "Benutzer verwalten",
    perm_manage_clients: "Clients verwalten", perm_automation: "Automation verwalten",
    // Audit
    audit_when: "Zeitpunkt", audit_user: "Benutzer", audit_action: "Aktion",
    audit_target: "Ziel", audit_details: "Details", audit_recording: "Aufzeichnung",
    view_recording: "▶ Aufzeichnung ansehen",
  },
  en: {
    login: "Sign in", username: "Username", password: "Password",
    logout: "Sign out", settings: "Settings", profile: "Profile",
    save: "Save", cancel: "Cancel", delete: "Delete", add: "Add",
    devices: "Devices", add_client: "+ Add client", not_assigned: "Unassigned",
    select_hint: "Select a tenant, location or client on the left.",
    no_devices_sidebar: "No devices yet. Add a client below.",
    manage_hierarchy: "Manage tenants & locations",
    status: "Status", status_online: "Online", status_offline: "Offline",
    status_maintenance: "Maintenance", status_crashed: "Crashed",
    os: "Operating system", ip: "IP address", hostname: "Hostname", arch: "Architecture",
    uptime: "Uptime", reboot: "Reboot", shutdown: "Shut down", edit: "Edit",
    update_agent: "Update agent",
    overview: "Overview", tab_metrics: "Metrics", tab_notes: "Notes", tab_disk: "Disk",
    notes_placeholder: "Notes for this client...",
    no_live_data: "No live data available (client offline).",
    cores: "cores", threads: "threads", free: "Free",
    network: "Network", time_range: "Time range",
    cpu_history: "CPU history", ram_history: "RAM history", network_history: "Network history",
    actions: "Actions", file_explorer: "File explorer", terminal: "Terminal",
    remote_screen: "Remote screen", task_manager: "Task manager",
    command_sent: "Command sent.", client_not_found: "Client not found.",
    online: "online", no_devices: "No devices in this selection.",
    no_location: "No location",
    // Start menu / app names
    menu_apps: "Applications", device_overview: "Device overview",
    tenants_locations: "Tenants & locations", scripts: "Scripts",
    bulk_shell: "Bulk Remote Shell", network_scanner: "Network scanner",
    recordings: "Recordings", tower_defense: "Tower Defense",
    audit_log: "Audit log", automation: "Automation", notifications: "Notifications",
    refresh: "Refresh", run: "Run", close: "Close",
    name: "Name", command: "Command", type: "Type", actions_col: "Actions",
    created: "Created", enabled: "Enabled", disabled: "Disabled",
    send: "Send", test: "Test", generate: "Generate",
    tab_general: "General", tab_users: "Users", tab_sso: "SSO / Directory",
    tab_notifications: "Notifications", tab_groups: "Groups & Roles",
    general_loading: "Loading settings…",
    general_server_title: "Server address (installation)",
    general_server_hint: "This address is embedded into the client install commands. Leave empty = derive automatically from the request (often ends up as \"localhost\" locally).",
    general_server_url: "Server URL / IP",
    general_metrics_title: "Metrics history",
    general_metrics_hint: "Controls how often graph data points are stored, how long they are kept, and how long replays are retained.",
    general_metrics_interval: "Save interval (seconds)",
    general_metrics_retention: "Graph retention (hours)",
    general_replay_retention: "Replay retention (days)",
    general_saved: "Settings saved",
    // Portscan
    portscan: "Port scan",
    ps_ip_placeholder: "IP address (e.g. 192.168.1.10)",
    ps_mode_standard: "Standard ports", ps_mode_all: "All ports (1–65535)", ps_mode_custom: "Custom ports",
    ps_scan: "Scan", ps_custom_placeholder: "e.g. 22,80,8000-8100",
    ps_need_ip: "Please enter an IP address.", ps_need_ports: "Please specify ports (e.g. 22,80,8000-8100).",
    ps_scanning: "Scanning ports…", ps_scanning_all: "Scanning all ports (may take a while)…",
    ps_ports_checked: "ports checked", ps_none: "No open ports found.",
    ps_open_found: "open ports found", ps_open_ports: "Open ports",
    // General tab: server address (extended)
    general_server_ip: "Server IP / host", general_server_domain: "Domain (optional)",
    general_backend_port: "Backend port", general_frontend_port: "Frontend port",
    general_server_url_advanced: "Full URL (overrides IP/domain/port)",
    auto_new: "New automation", auto_schedule: "Schedule", auto_target: "Target clients",
    auto_every: "Run every", minutes: "minutes", hours: "hours", days: "days",
    auto_none: "No automations yet.",
    notif_webhook_url: "Webhook URL", notif_type: "Webhook type",
    notif_discord: "Discord", notif_custom: "Custom",
    notif_none: "No webhooks configured yet.",
    notif_test_sent: "Test notification sent.",
    sso_intro: "Connect an external directory (e.g. Active Directory) to delegate logins.",
    sso_realm_name: "Realm name", sso_server: "Server (host/IP)", sso_base_dn: "Base DN",
    sso_bind_user: "Bind user (DN)", sso_bind_pw: "Bind password",
    sso_none: "No directories connected yet.",
    sso_add_realm: "Add directory / realm", sso_edit_realm: "Edit realm",
    sso_port: "Port (optional)", sso_use_ssl: "LDAPS (SSL)",
    sso_pw_unchanged: "(leave unchanged)", sso_user_filter: "Additional filter (optional)",
    sso_filter_hint: "Optional LDAP filter to allow only certain users (combined with the default filter).",
    sso_connected: "Connected directories", sso_enable: "Enable", sso_disable: "Disable",
    sso_test_ok: "Connection successful – configuration is valid.",
    sso_test_fail: "Test failed:",
    sso_delete_confirm: "Really delete this directory?",
    sso_group_hint: "AD users get their permissions via RMM groups that have the same names as their AD groups. Create matching groups under the \"Groups & Roles\" tab.",
    groups_intro: "Groups bundle permissions. Assign a role to users or AD groups.",
    role: "Role", permissions: "Permissions", group_name: "Group / AD group name",
    perm_login: "Sign in", perm_screen: "Remote screen", perm_terminal: "Terminal",
    perm_explorer: "File explorer", perm_quick: "Quick actions (reboot etc.)",
    perm_audit: "View audit log", perm_manage_users: "Manage users",
    perm_manage_clients: "Manage clients", perm_automation: "Manage automation",
    audit_when: "When", audit_user: "User", audit_action: "Action",
    audit_target: "Target", audit_details: "Details", audit_recording: "Recording",
    view_recording: "▶ View recording",
  },
};

let currentLang = localStorage.getItem("rmm_lang") || "de";

export function setLanguage(lang) {
  currentLang = translations[lang] ? lang : "de";
  localStorage.setItem("rmm_lang", currentLang);
}

export function getLanguage() {
  return currentLang;
}

// Übersetzt einen Schlüssel. Fällt auf Deutsch zurück, dann auf den Schlüssel
// selbst, falls eine Übersetzung fehlt (so sieht man sofort, was noch fehlt).
export function t(key) {
  return translations[currentLang]?.[key] ?? translations.de[key] ?? key;
}

// Kleines OS-Symbol (Emoji) je nach Plattform - für die Status-/Client-Anzeige
// Kleines OS-Symbol je nach Plattform/Distribution. Bekommt platform (z.B.
// "Linux", "Windows") und optional release (z.B. "Ubuntu 22.04", "Windows 11").
// Da es keine offiziellen Emojis für jede Distribution gibt, nutzen wir
// charakteristische Symbole, die die Systeme gut unterscheidbar machen.
export function osIcon(platform, release = "") {
  const p = (platform || "").toLowerCase();
  const r = (release || "").toLowerCase();
  const both = p + " " + r;

  // --- Linux-Distributionen anhand des Release-Strings erkennen ---
  if (both.includes("debian")) return "🌀";        // Debian (Spirale ~ Logo)
  if (both.includes("ubuntu")) return "🟠";        // Ubuntu (orange Kreis)
  if (both.includes("proxmox") || both.includes("pve")) return "🧱"; // Proxmox VE
  if (both.includes("raspbian") || both.includes("raspberry")) return "🍓"; // Raspberry Pi OS
  if (both.includes("fedora")) return "🎩";        // Fedora (Hut)
  if (both.includes("arch")) return "🏹";          // Arch (Bogen)
  if (both.includes("centos") || both.includes("red hat") || both.includes("rhel")) return "🎯"; // RHEL/CentOS
  if (both.includes("suse")) return "🦎";          // (open)SUSE (Chamäleon)
  if (both.includes("alpine")) return "🏔️";        // Alpine

  // --- Windows-Versionen ---
  if (p.includes("windows")) {
    if (r.includes("11")) return "🪟";             // Windows 11
    if (r.includes("10")) return "🪟";             // Windows 10
    if (r.includes("server")) return "🖥️";         // Windows Server
    return "🪟";
  }

  // --- macOS ---
  if (p.includes("darwin") || p.includes("mac")) return "🍎";

  // --- Generisches Linux / Fallback ---
  if (p.includes("linux")) return "🐧";
  return "💻";
}

// Menschenlesbarer OS-Name (z.B. "Ubuntu 22.04" statt "Linux") für die Anzeige.
export function osLabel(platform, release = "") {
  const r = (release || "").trim();
  const p = (platform || "").trim();
  // Wenn der Release-String schon den Distributionsnamen enthält, diesen nehmen
  if (r && /ubuntu|debian|proxmox|raspbian|fedora|arch|centos|suse|alpine|windows/i.test(r)) {
    return r;
  }
  return [p, r].filter(Boolean).join(" ") || "?";
}
