"""
Skripta koja automatski objavljuje sledeci video (reel) sa Google Drive foldera
na Instagram, u krug (rotation). Pre objave, na video dodaje tekst:
- gornji deo: nasumicno izabrana poruka sa liste
- donji deo: fiksna cena/poruka

Obradjeni video se privremeno otprema na Cloudinary (besplatan servis za
hostovanje), jer Instagram zahteva javni link do videa da bi ga objavio.

Ne treba ovo pokretati rucno -- GitHub Actions to radi sam, po rasporedu.
"""

import os
import json
import time
import random
import subprocess
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
STATE_FILE = "state.json"
GRAPH_VERSION = "v21.0"
API_BASE = "https://graph.instagram.com"

CLOUDINARY_CLOUD_NAME = "dnbjvccgy"
CLOUDINARY_UPLOAD_PRESET = "instagram_bot"

IG_CAPTION = (
    "Napravi haos u drustvu sa #vamit5sat - Samo 19e danas! "
    "Link ka Online Shopu je u opisu profila."
)

BOTTOM_TEXT = "Danas samo 19e"

TOP_TEXTS = [
    "Napravi haos u drustvu",
    "Da li mozes pobediti VAMIT-5 sat",
    "99% ljudi ne uspe VAMIT-5 sat do kraja",
]

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Pozicije teksta kao procenat visine videa (0 = vrh, 1 = dno)
TOP_TEXT_Y_FRACTION = 0.14
BOTTOM_TEXT_Y_FRACTION = 0.80


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
    query = (
        f"'{folder_id}' in parents and mimeType contains 'video/' and trashed=false"
    )
    results = (
        service.files()
        .list(q=query, fields="files(id, name, createdTime)", orderBy="createdTime")
        .execute()
    )
    return results.get("files", [])


def download_file(service, file_id, local_path):
    request = service.files().get_media(fileId=file_id)
    with open(local_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"Preuzimanje: {int(status.progress() * 100)}%")


def get_video_dimensions(local_path):
    """Vraca STVARNE (prikazane) dimenzije videa, uzimajuci u obzir
    rotacione metapodatke koje telefoni cesto upisuju (video snimljen
    'uspravno' moze biti sacuvan sa sirinom/visinom obrnutim, plus oznakom
    da ga treba rotirati pri prikazu)."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
            "-of", "json",
            local_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    width = stream["width"]
    height = stream["height"]

    rotation = 0
    tags = stream.get("tags", {})
    if "rotate" in tags:
        rotation = int(tags["rotate"])
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            rotation = int(sd["rotation"])

    rotation = rotation % 360
    if rotation in (90, 270):
        width, height = height, width

    return width, height


def escape_ffmpeg_text(text):
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
    )


def compute_fontsize(text, width):
    size = int(width / (max(len(text), 10) * 0.5))
    return max(40, min(90, size))


def add_text_overlay(local_in, local_out, width, height):
    top_text = random.choice(TOP_TEXTS)
    bottom_text = BOTTOM_TEXT

    top_size = compute_fontsize(top_text, width)
    bottom_size = compute_fontsize(bottom_text, width)

    top_escaped = escape_ffmpeg_text(top_text)
    bottom_escaped = escape_ffmpeg_text(bottom_text)

    top_y = int(height * TOP_TEXT_Y_FRACTION)
    bottom_y = int(height * BOTTOM_TEXT_Y_FRACTION)

    drawtext_top = (
        f"drawtext=fontfile={FONT_PATH}:text='{top_escaped}':"
        f"fontsize={top_size}:fontcolor=white:"
        f"x=(w-text_w)/2:y={top_y}:"
        f"box=1:boxcolor=black@0.55:boxborderw=20"
    )
    drawtext_bottom = (
        f"drawtext=fontfile={FONT_PATH}:text='{bottom_escaped}':"
        f"fontsize={bottom_size}:fontcolor=white:"
        f"x=(w-text_w)/2:y={bottom_y}:"
        f"box=1:boxcolor=black@0.55:boxborderw=20"
    )

    filter_chain = f"{drawtext_top},{drawtext_bottom}"

    cmd = [
        "ffmpeg", "-y",
        "-i", local_in,
        "-vf", filter_chain,
        "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
        "-c:a", "copy",
        local_out,
    ]
    print("Pokrecem ffmpeg:", " ".join(cmd))
    subprocess.run(cmd, check=True)


def upload_to_cloudinary(local_path):
    url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/video/upload"
    with open(local_path, "rb") as f:
        files = {"file": f}
        data = {"upload_preset": CLOUDINARY_UPLOAD_PRESET}
        r = requests.post(url, files=files, data=data, timeout=300)
    if not r.ok:
        print("Greska pri otpremanju na Cloudinary:", r.text)
    r.raise_for_status()
    return r.json()["secure_url"]


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

    print(f"Redosled: {next_index + 1}/{len(videos)} -- obradjujem: {video['name']}")

    local_in = "original.mp4"
    local_out = "sa_tekstom.mp4"

    download_file(drive, video["id"], local_in)
    width, height = get_video_dimensions(local_in)
    print(f"Dimenzije videa (posle rotacije): {width}x{height}")
    add_text_overlay(local_in, local_out, width, height)

    video_url = upload_to_cloudinary(local_out)
    print(f"Video otpremljen na: {video_url}")

    container_id = create_media_container(ig_user_id, access_token, video_url, IG_CAPTION)
    wait_for_container(container_id, access_token)
    result = publish_container(ig_user_id, access_token, container_id)

    print(f"Uspesno objavljeno! Media ID: {result.get('id')}")

    state["last_index"] = next_index
    save_state(state)


if __name__ == "__main__":
    main()
