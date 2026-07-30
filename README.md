# RAPALLE.net RMM

RAPALLE.net RMM is a self-developed Remote Monitoring and Management (RMM) platform designed for technology enthusiasts, homelab operators, and individuals who manage their own servers and infrastructure projects.

The software provides tools for monitoring systems, managing devices remotely, executing administrative tasks, and maintaining an overview of small to medium-sized self-hosted environments.

## Important Notice

RAPALLE.net RMM is a personal project and is not intended for enterprise, commercial, or regulated environments. It has not been designed, tested, or certified to comply with specific industry standards, security frameworks, legal requirements, or regulatory obligations.

While every effort is made to improve reliability and stability, software bugs, unexpected behavior, security issues, data loss, service interruptions, or other problems may occur.

**Use this software entirely at your own risk.**

By using RAPALLE.net RMM, you acknowledge that:

- The software is provided "as is" without warranties of any kind.
- Functionality may change without notice.
- Errors and unexpected behavior can occur.
- The software may not be suitable for production or business-critical systems.
- The author assumes no responsibility for any damage, downtime, data loss, security incidents, or other consequences resulting from its use.

This project is best suited for learning, experimentation, homelabs, and personal infrastructure environments where users are comfortable managing and accepting the risks associated with self-hosted software.

Please keep in mind that parts of this project were vibe-coded, which means that it was done with artificial intelligenz.





## Installation

To install RAPALLE.net RMM on your own server, download the latest release as a ZIP archive and extract it to a directory of your choice, and run the run.py file.

### Requirements

- Python 3.11 or newer
- Administrative/root privileges
- Internet connection for initial dependency installation

### Installation Steps

1. Download and extract the latest release.
2. Open a terminal with admin/root privilages in the backend sub-directory.
3. Start the application using: python run.py
4. On first startup, RAPALLE.net RMM automatically creates all required files, initializes the database, and installs any missing Python dependencies.
5. Open a browsertab with http://YOUR-SERVERS-IP:4000; standart account is user: admin, password: admin.

6. Please consider changing the AGENT_TOKEN and JWT_SECRET in the env file inside the backend and agent folders.

### Installation with Docker

A `Dockerfile` and a complete `docker-compose.yml` are included.

```
cp .env.example .env        # optional: change port / timezone
docker compose up -d --build
```

Then open `http://YOUR-SERVERS-IP:4000` (default login `admin` / `admin`).

The project folders `backend/`, `frontend/` and `agent/` are mounted into the
container as bind mounts. This keeps the database, recordings and your `.env`
outside the image and keeps the built-in source editor and the self-update
feature working.

An optional `guacd` service is included for browser-based RDP/VNC/SSH sessions.
Remove it from `docker-compose.yml` if you do not need remote sessions.

#### What cannot be changed at runtime in a container

Inside a container some settings are fixed the moment the container starts:

- **PORT** and **HOST** — the port mapping is defined in `docker-compose.yml`.
  Changing `PORT` in `backend/.env` will lock you out, because the container
  keeps forwarding the old port. To change the port, edit `RMM_PORT` in `.env`
  and run `docker compose up -d` again.
- **Volumes / data paths** — anything not stored in a mounted folder is lost
  when the image is rebuilt.

The web interface shows this as well: **Settings → Source** displays whether
the backend is *installed as a Docker container* or *installed as a program*,
which address it is bound to, and which settings are locked.






## Support & Feedback

Feel free to join the RAPALLE.net Discord community:

https://dc.rapalle.net

You can open a ticket in the **Tickets** section to get in touch directly with the developer and discuss issues, suggestions, feature requests, or general questions about RAPALLE.net RMM.

