// --------------------------------------------------
// Elemente
// --------------------------------------------------

const form = document.getElementById("login-form");
const passwordInput = document.getElementById("password");
const usernameInput = document.getElementById("username");
const realmInput = document.getSelection("realm");
const error = document.getElementById("error");


// --------------------------------------------------
// Anwendung laden
// --------------------------------------------------

function loadApp() {

    // ----------------------------------------------
    // Login-CSS entfernen
    // ----------------------------------------------

    const loginCss = document.getElementById("login-css");

    if (loginCss) {
        loginCss.remove();
    }


    // ----------------------------------------------
    // App-CSS laden
    // ----------------------------------------------

    const appCss = document.createElement("link");

    appCss.rel = "stylesheet";
    appCss.href = "/css/app.css";

    document.head.appendChild(appCss);


    // ----------------------------------------------
    // main.js laden
    // ----------------------------------------------

    const mainScript = document.createElement("script");

    mainScript.src = "/js/main.js";

    mainScript.onload = () => {
        console.log("main.js wurde geladen.");
    };

    mainScript.onerror = () => {
        console.error("main.js konnte nicht geladen werden.");
    };

    document.body.appendChild(mainScript);
}


// --------------------------------------------------
// Prüfen, ob bereits eingeloggt
// --------------------------------------------------

async function checkLogin() {

    try {

        const response = await fetch("/api/check-login", {
            method: "GET"
        });

        if (!response.ok) {
            return;
        }

        const data = await response.json();

        if (data.logged_in) {

            console.log("Benutzer ist bereits eingeloggt.");

            loadApp();
        }

    } catch (error) {

        console.error(
            "Login-Status konnte nicht überprüft werden:",
            error
        );
    }
}


// --------------------------------------------------
// Login
// --------------------------------------------------

form.addEventListener("submit", async (event) => {

    event.preventDefault();

    error.textContent = "";

    document.sele

    const password = passwordInput.value;
    const username = usernameInput.value;
    const realm = realmInput;


    try {

        const response = await fetch("/api/login", {
            method: "POST",

            headers: {
                "Content-Type": "application/json"
            },

            body: JSON.stringify({
                password: password,
                username: username,
                realm: realm
            })
        });


        const data = await response.json();


        // ------------------------------------------
        // Passwort falsch
        // ------------------------------------------

        if (!response.ok) {

            error.textContent = data.message;

            usernameInput.focus();

            return;
        }


        // ------------------------------------------
        // Login erfolgreich
        // ------------------------------------------

        console.log("Login erfolgreich.");

        loadApp();

    } catch (err) {

        console.error(err);

        error.textContent =
            "Server nicht erreichbar.";
    }
});


// --------------------------------------------------
// Beim Start Login prüfen
// --------------------------------------------------

checkLogin();