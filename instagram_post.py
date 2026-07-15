"""
Skripta koja automatski objavljuje sledeci video (reel) sa Google Drive foldera
na Instagram, u krug (rotation). Cuva u state.json koji je video poslednji
objavljen, tako da svaki sledeci pokretanje objavi SLEDECI video na listi.

Ne treba ovo pokretati rucno -- GitHub Actions to radi sam, po rasporedu.
"""

import os
import json
import time
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive"]
STATE_FILE = "state.json"
GRAPH_VERSION = "v21.0"
API_BASE = "https://graph.instagram.com"


def get_drive_service():
    creds_json = os.environ["GDRIVE_SERVICE_ACCOUNT_JSON"]
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds)


def list_videos(service, folder_id):
    """Vraca listu video fajlova u folderu, sortiranu po datumu dodavanja
    (najstariji prvi), tako da je redosled objavljivanja predvidiv."""
    query = f"'{folder_id}' in parents and mimeType contains 'video/' and trashed=false"
    results = (
        service.files()
        .list(q=query, fields="files(id, name, createdTime)", orderBy="createdTime")
        .execute()
    )
    return results.get("files", [])


def make_public(service, file_id):
    """Instagram mora da moze da 'skine' video preko javnog linka, pa fajl
    privremeno postavljamo na 'bilo ko sa linkom moze da gleda'."""
    try:
        service.permissions().create(
            fileId=file_id, body={"type": "anyone", "role": "reader"}
        ).execute()
    except Exception as e:
        print(f"Upozorenje pri postavljanju dozvole: {e}")


def get_direct_url(file_id):
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_index": -1}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def create_media_container(ig_user_id, access_token, video_url, caption=""):
    url = f"{API_BASE}/{GRAPH_VERSION}/{ig_user_id}/media"
    payload = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": access_token,
    }
    r = requests.post(url, data=payload, timeout=60)
    if not r.ok:
        print("Greska pri kreiranju medija:", r.text)
    r.raise_for_status()
    return r.json()["id"]


def wait_for_container(container_id, access_token, timeout=600):
    """Instagram treba vremena da obradi video pre objave -- proveravamo
    na svakih 10 sekundi da li je gotovo (status FINISHED)."""
    url = f"{API_BASE}/{GRAPH_VERSION}/{container_id}"
    start = time.time()
    while time.time() - start < timeout:
        r = requests.get(
            url, params={"fields": "status_code", "access_token": access_token}, timeout=30
        )
        r.raise_for_status()
        status = r.json().get("status_code")
        print(f"Status obrade: {status}")
        if status == "FINISHED":
            return True
        if status == "ERROR":
            raise RuntimeError("Instagram je prijavio gresku pri obradi videa.")
        time.sleep(10)
    raise TimeoutError("Isteklo je vreme cekanja na obradu videa.")


def publish_container(ig_user_id, access_token, container_id):
    url = f"{API_BASE}/{GRAPH_VERSION}/{ig_user_id}/media_publish"
    payload = {"creation_id": container_id, "access_token": access_token}
    r = requests.post(url, data=payload, timeout=60)
    if not r.ok:
        print("Greska pri objavljivanju:", r.text)
    r.raise_for_status()
    return r.json()


def main():
    access_token = os.environ["IG_ACCESS_TOKEN"]
    ig_user_id = os.environ["IG_ACCOUNT_ID"]
    folder_id = os.environ["GDRIVE_FOLDER_ID"]

    drive = get_drive_service()
    videos = list_videos(drive, folder_id)

    if not videos:
        print("Nema video fajlova u Google Drive folderu. Preskacem.")
        return

    state = load_state()
    next_index = (state["last_index"] + 1) % len(videos)
    video = videos[next_index]

    print(f"Redosled: {next_index + 1}/{len(videos)} -- objavljujem: {video['name']}")

    make_public(drive, video["id"])
    video_url = get_direct_url(video["id"])

    container_id = create_media_container(ig_user_id, access_token, video_url)
    wait_for_container(container_id, access_token)
    result = publish_container(ig_user_id, access_token, container_id)

    print(f"Uspesno objavljeno! Media ID: {result.get('id')}")

    state["last_index"] = next_index
    save_state(state)


if __name__ == "__main__":
    main()
