# Minimal Torrent Browser

Simple local app with:
- HTML table of torrent downloads (name, progress %, ratio)
- direct file/folder links from your downloads directory
- Jackett-powered search
- one-click add torrent to qBittorrent

## Services
- App: `http://localhost:5000`
- qBittorrent WebUI: `http://localhost:8080`
- Jackett: `http://localhost:9117`

## Requirements
- Docker + Docker Compose
- Python 3.10+ (for local, non-Docker run)

## Downloads folder

Everything that touches files on disk (this app, qBittorrent, Jackett) is wired to **one shared folder**. Inside containers that folder is always mounted at **`/downloads`**; on your machine you choose what that maps to.

### Docker: default (easiest)

The compose file uses **`./downloads`** next to this project (same directory as `docker-compose.yml`). Create it once if it does not exist:

```bash
mkdir -p downloads
```

### Docker: use your own folder

1. Pick where torrents should live on the host, e.g. `/mnt/media/torrents` (Linux/macOS) or `D:/Torrents` (Windows paths in Compose are easiest as forward slashes).
2. Open **`docker-compose.yml`** and find **every** volume line of the form `- ./downloads:/downloads` (there is one under `jackett`, one under `qbittorrent`, and one under `app`).
3. Change **only the left side** to your path, and use the **same** host path on all three lines. The part after the colon must stay **`/downloads`** (that is the path *inside* the containers).

Example after editing (all three services should match):

```yaml
- /mnt/media/torrents:/downloads
```

4. In the qBittorrent Web UI, set the default save location to **`/downloads`** (or a subfolder like **`/downloads/tv`**) under **Settings → Downloads**. That path is **inside the container**; it is the same place as the host folder you mapped in step 3.

### Local Python (app only)

The app reads **`DOWNLOADS_DIR`** (default in code is `/downloads`, which is meant for Docker). Point it at a real folder on your machine before starting the app, for example:

```bash
export DOWNLOADS_DIR=/path/to/your/torrents
python app/app.py
```

If you already load variables from `.env`, you can add a line `DOWNLOADS_DIR=/path/to/your/torrents` there instead. qBittorrent must still save torrents into that same folder (or the file links in the app will not match).

## Setup
1. Copy env file: `cp .env.example .env`
2. Start the stack (use `--build` after changing the app; otherwise `docker compose up -d` is enough):
   ```bash
   docker compose up -d --build
   ```
3. **qBittorrent WebUI (first run only)**  
   qBittorrent 4.6+ does not ship with `admin` / `adminadmin`. The LinuxServer image prints a **temporary** password in the container log on first start.
   - Run `docker logs qbittorrent` and find the line that mentions the temporary password (username is `admin`).
   - Open `http://localhost:8080`, sign in with `admin` and that password.
   - Go to **Settings → Web UI → Authentication**, set a **permanent** username and password, and save.
   - Put the same values in `.env` as `QBIT_USER` and `QBIT_PASS`.  
   After you set a permanent password in the Web UI, restarts will not keep generating new random passwords.
4. **Jackett:** Open `http://localhost:9117`, copy your API key into `.env` as `JACKETT_API_KEY`. Add indexers in Jackett if you want search results.
5. Apply `.env` changes to the app container: `docker compose restart app`

Then open `http://localhost:5000`.

## Local Python venv workflow (required for local app run)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r app/requirements.txt
```

Run the app locally (while Jackett and qBittorrent are running):
```bash
export $(grep -v '^#' .env | xargs)
python app/app.py
```

## Notes
- Download links are served from the configured downloads directory (see **Downloads folder** above) via `/files/...`.
- Clicking a folder opens a simple directory listing.
- Clicking a file downloads it directly.
