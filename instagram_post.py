"""
Skripta koja automatski objavljuje sledeci video (reel) sa Google Drive foldera
na Instagram, u krug (rotation). Pre objave, na video dodaje tekst:
- gornji deo: nasumicno izabrana poruka (do 2 reda)
- donji deo: nasumicno izabrana cena/poruka (1 red)

Isti tekstovi (ali sa emotikonima, koji na videu ne rade pouzdano) se
koriste i za opis (caption) ispod objave na Instagramu.

Obradjeni video se privremeno otprema na Cloudinary (besplatan servis za
hostovanje), jer Instagram zahteva javni link do videa da bi ga objavio.

Ne treba ovo pokretati rucno -- GitHub Actions to radi sam, po rasporedu.
"""

import os
import json
import re
import time
import random
import textwrap
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

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Pozicije teksta kao procenat visine videa (0 = vrh, 1 = dno) -- potvrdjeno
# da su ove pozicije dobre, ne diraj bez razloga.
TOP_TEXT_Y_FRACTION = 0.14
BOTTOM_TEXT_Y_FRACTION = 0.80

# Velicina slova na videu -- veci base = veca pocetna velicina, skripta je
# sama smanjuje samo ako mora da stane u dozvoljen broj redova.
FONT_BASE_SIZE = 120
FONT_MIN_SIZE = 56
FONT_WIDTH_FACTOR = 0.5

# Ovi tekstovi se koriste NA VIDEU (bez emotikona) i, sa emotikonima, u
# opisu (caption) ispod objave. Napomena: neki pominju dete, a skripta ne
# moze da prepozna da li dete stvarno postoji na snimku -- ubaceni su na
# izricit zahtev, pa ce se povremeno pojaviti i na snimcima bez dece.
TOP_TEXTS = [
    "Da li ćeš preživeti ceo VAMIT-5 sat? 😱",
    "99% ljudi ne uspe kompletan VAMIT-5 sat ❌",
    "Napravi haos u društvu sa VAMIT-5 satom 🕐",
    "VAMIT-5 sat napravio haos na Balkanu 😱🕐",
    "Ljudi poludeli za VAMIT-5 satom 😱🕐",
    "Najtraženiji fitnes proizvod u regiji 🕐😍",
    "Idealan proizvod za trenere i njegove klijente 😱",
    "Napravi nezaboravnu žurku sa VAMIT-5 satom 🕐😍",
    "Tvoje dete će se obradovati ovom satu 😍",
    "Vreme je da pokloniš ovaj sat svom detetu 😍",
    "Pokloni ovaj sat svom detetu, unuku i gledaj napade sreće 🕐😍",
    "Tvoje dete te tajno moli da mu kupiš ovaj sat 🕐😍",
]

BOTTOM_TEXTS = [
    "Poruči danas za samo 19€",
    "Danas samo 19€ (Link u BIO)",
    "Još samo danas 19€",
    "Dostava širom Evrope 😍",
    "Poruči danas - stiže brzo 🕐",
]


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
    rotacione metapodatke koje telefoni cesto upisuju."""
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


EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\uFE0F"
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text):
    return EMOJI_PATTERN.sub("", text).strip()


def escape_ffmpeg_text(text):
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
    )


def wrap_and_fit(text, width, max_lines):
    """Smanjuje velicinu slova dok tekst ne stane u najvise max_lines
    redova, bez sece/preklapanja."""
    fontsize = FONT_BASE_SIZE
    while fontsize >= FONT_MIN_SIZE:
        chars_per_line = max(6, int(width / (fontsize * FONT_WIDTH_FACTOR)))
        lines = textwrap.wrap(text, width=chars_per_line)
        if len(lines) <= max_lines:
            return lines, fontsize
        fontsize -= 3

    chars_per_line = max(6, int(width / (FONT_MIN_SIZE * FONT_WIDTH_FACTOR)))
    lines = textwrap.wrap(text, width=chars_per_line)[:max_lines]
    return lines, FONT_MIN_SIZE


def build_drawtext(lines, fontsize, y_fraction, height):
    text_with_breaks = "\n".join(escape_ffmpeg_text(line) for line in lines)
    y = int(height * y_fraction)
    return (
        f"drawtext=fontfile={FONT_PATH}:text='{text_with_breaks}':"
        f"fontsize={fontsize}:fontcolor=white:line_spacing=10:"
        f"x=(w-text_w)/2:y={y}:"
        f"box=1:boxcolor=black@0.55:boxborderw=20"
    )


def add_text_overlay(local_in, local_out, width, height, top_original, bottom_original):
    top_text = strip_emoji(top_original)
    bottom_text = strip_emoji(bottom_original)

    top_lines, top_size = wrap_and_fit(top_text, width, max_lines=2)
    bottom_lines, bottom_size = wrap_and_fit(bottom_text, width, max_lines=1)

    drawtext_top = build_drawtext(top_lines, top_size, TOP_TEXT_Y_FRACTION, height)
    drawtext_bottom = build_drawtext(bottom_lines, bottom_size, BOTTOM_TEXT_Y_FRACTION, height)

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


def build_caption(top_original, bottom_original):
    return (
        f"{top_original}\n"
        f"{bottom_original}\n\n"
        f"#vamit5sat\n"
        f"Link ka Online Shopu je u opisu profila!"
    )


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

    top_original = random.choice(TOP_TEXTS)
    bottom_original = random.choice(BOTTOM_TEXTS)

    local_in = "original.mp4"
    local_out = "sa_tekstom.mp4"

    download_file(drive, video["id"], local_in)
    width, height = get_video_dimensions(local_in)
    print(f"Dimenzije videa (posle rotacije): {width}x{height}")
    add_text_overlay(local_in, local_out, width, height, top_original, bottom_original)

    video_url = upload_to_cloudinary(local_out)
    print(f"Video otpremljen na: {video_url}")

    caption = build_caption(top_original, bottom_original)

    container_id = create_media_container(ig_user_id, access_token, video_url, caption)
    wait_for_container(container_id, access_token)
    result = publish_container(ig_user_id, access_token, container_id)

    print(f"Uspesno objavljeno! Media ID: {result.get('id')}")

    state["last_index"] = next_index
    save_state(state)


if __name__ == "__main__":
    main()
