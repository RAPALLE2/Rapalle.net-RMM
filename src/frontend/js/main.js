// --------------------------------------------------
// Hauptanwendung
// --------------------------------------------------

document.body.innerHTML = `

    <header class="app-header">

        <h1>Meine Anwendung</h1>

        <button id="logout-button">
            Logout
        </button>

    </header>


    <main class="app-main">

        <h2>Willkommen!</h2>

        <p>
            Du bist erfolgreich eingeloggt.
        </p>


        <button id="load-data-button">
            Daten vom Backend laden
        </button>


        <div id="data-container">
        </div>

    </main>

`;


// --------------------------------------------------
// Daten vom Backend laden
// --------------------------------------------------

const loadDataButton =
    document.getElementById("load-data-button");

const dataContainer =
    document.getElementById("data-container");


loadDataButton.addEventListener("click", async () => {

    try {

        const response = await fetch("/api/data");


        // ------------------------------------------
        // Session nicht mehr gültig
        // ------------------------------------------

        if (response.status === 401) {

            window.location.reload();

            return;
        }


        const data = await response.json();


        // ------------------------------------------
        // Daten anzeigen
        // ------------------------------------------

        dataContainer.innerHTML = `

            <div class="data-box">

                <h3>Backend-Daten</h3>

                <p>
                    ${data.message}
                </p>

                <p>
                    Name: ${data.data.name}
                </p>

                <p>
                    Punkte: ${data.data.punkte}
                </p>

            </div>

        `;

    } catch (error) {

        console.error(error);

        dataContainer.textContent =
            "Fehler beim Laden der Daten.";
    }
});


// --------------------------------------------------
// Logout
// --------------------------------------------------

const logoutButton =
    document.getElementById("logout-button");


logoutButton.addEventListener("click", async () => {

    try {

        const response = await fetch("/api/logout", {
            method: "POST"
        });


        if (response.ok) {

            // Seite neu laden
            // Dadurch erscheint wieder der Login.

            window.location.reload();
        }

    } catch (error) {

        console.error(
            "Logout fehlgeschlagen:",
            error
        );
    }
});