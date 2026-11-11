# SouthpawOS dashboard - Member E (Jovis)

Two parts:

| Folder | Runs on | What it is |
|---|---|---|
| [frontend/](frontend/) | A server, under Docker Compose | The website and its database. Coaches and athletes log in here |
| [backend/](backend/) | The Raspberry Pi | Sends each finished round and the player's NFC card ID to the website |

    Pi: hub -> backend/pi_uploader.py  --POST /api/round after every round-->  frontend: website <-> database

Quick start on one laptop, no Docker and no hardware:

    cd frontend
    pip install -r requirements.txt
    INGEST_TOKEN=dev-token python app.py --sim        # website with demo data on http://localhost:5000
                                                      # (PowerShell: $env:INGEST_TOKEN='dev-token'; python app.py --sim)
    cd ../backend
    python pi_uploader.py --token dev-token --card 04A2B91C     # plays a made-up session as the demo athlete Kai

Each folder has its own README with the details.
