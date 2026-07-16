"""
Skripta koja automatski objavljuje sledeci video (reel) sa Google Drive foldera
na Instagram, u krug (rotation). Pre objave, na video dodaje tekst SA PRAVIM
emoji slicicama (preuzetim sa interneta), jer ffmpeg sam po sebi ne ume da
iscrta emotikone u boji. Python prvo nacrta ceo natpis (tekst + emoji) kao
providnu PNG sliku, tacno izmerenu da stane u zadati broj redova i da bude
centrirana, pa se ta slika "zalepi" preko videa.

Ne treba ovo pokretati rucno -- GitHub Actions to radi sam, po rasporedu.
"""

import os
import io
import json
import re
import time
import random
import subprocess
import requests
from PIL import Image, ImageDraw, ImageFont
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
TWEMOJI_BASE = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/"

TOP_TEXT_Y_FRACTION = 0.14
BOTTOM_TEXT_Y_FRACTION = 0.80

FONT_BASE_SIZE = 90
FONT_MIN_SIZE = 40
BOX_BORDER = 24
BOX_RADIUS = 18
BOX_COLOR = (0, 0, 0, 140)
TEXT_COLOR = (255, 255, 255, 255)
LINE_HEIGHT_FACTOR = 1.35
MAX_TEXT_WIDTH_FRACTION = 0.86

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


def tokenize(text):
    """Deli tekst na 'reci' i 'emoji grupe', cuvajuci redosled, da bi
    moglo da se meri i prelama red po red uzimajuci u obzir oboje."""
    tokens = []
    pos = 0
    for m in EMOJI_PATTERN.finditer(text):
        before = text[pos:m.start()]
        for w in before.split():
            tokens.append({"text": w, "emoji": False})
        tokens.append({"text": m.group(), "emoji": True})
        pos = m.end()
    for w in text[pos:].split():
        tokens.append({"text": w, "emoji": False})
    return tokens


def token_width(token, font, fontsize):
    if token["emoji"]:
        return fontsize * len(token["text"])
    return font.getlength(token["text"])


def line_width(line, font, fontsize, space_w):
    total = 0
    for i, tok in enumerate(line):
        total += token_width(tok, font, fontsize)
        if i < len(line) - 1:
            total += space_w
    return total


def wrap_tokens(tokens, font, fontsize, max_width_px):
    space_w = font.getlength(" ")
    lines = []
    current = []
    current_w = 0
    for tok in tokens:
        w = token_width(tok, font, fontsize)
        added = w if not current else w + space_w
        if current and current_w + added > max_width_px:
            lines.append(current)
            current = [tok]
            current_w = w
        else:
            current.append(tok)
            current_w += added
    if current:
        lines.append(current)
    return lines
    def fit_tokens(text, video_width, max_lines):
    
    
    
    
    max_width_px = int(video_width * MAX_TEXT_WIDTH_FRACTION) - (2 * BOX_BORDER)
    max_width_px = max(max_width_px, 50)
    tokens = tokenize(text)

    fontsize = FONT_BASE_SIZE
    while fontsize >= FONT_MIN_SIZE:
        font = ImageFont.truetype(FONT_PATH, fontsize)
        lines = wrap_tokens(tokens, font, fontsize, max_width_px)
        space_w = font.getlength(" ")
        fits = all(line_width(l, font, fontsize, space_w) <= max_width_px for l in lines)
        if len(lines) <= max_lines and fits:
            return lines, fontsize
        fontsize -= 2

    font = ImageFont.truetype(FONT_PATH, FONT_MIN_SIZE)
    lines = wrap_tokens(tokens, font, FONT_MIN_SIZE, max_width_px)[:max_lines]
    return lines, FONT_MIN_SIZE


def get_emoji_image(char, size, cache):
    codepoint = format(ord(char), "x")
    key = (codepoint, size)
    if key in cache:
        return cache[key]
    url = TWEMOJI_BASE + codepoint + ".png"
    img = None
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGBA")
        img = img.resize((size, size), Image.LANCZOS)
    except Exception as e:
        print(f"Ne mogu da preuzmem emoji ({char}): {e}")
    cache[key] = img
    return img


def render_caption_image(lines, fontsize, emoji_cache):
    font = ImageFont.truetype(FONT_PATH, fontsize)
    space_w = font.getlength(" ")
    line_height = int(fontsize * LINE_HEIGHT_FACTOR)

    widths = [line_width(l, font, fontsize, space_w) for l in lines]
    content_width = int(max(widths)) if widths else 0
    content_height = line_height * len(lines)

    img_w = content_width + 2 * BOX_BORDER
    img_h = content_height + 2 * BOX_BORDER

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, img_w, img_h], radius=BOX_RADIUS, fill=BOX_COLOR)

    y = BOX_BORDER
    for line, lw in zip(lines, widths):
        x = BOX_BORDER + (content_width - lw) / 2
        for tok in line:
            if tok["emoji"]:
                for ch in tok["text"]:
                    em_img = get_emoji_image(ch, fontsize, emoji_cache)
                    if em_img is not None:
                        paste_y = int(y + (line_height - fontsize) / 2)
                        img.paste(em_img, (int(x), paste_y), em_img)
                    x += fontsize
            else:
                draw.text((x, y + (line_height - fontsize) / 2), tok["text"], font=font, fill=TEXT_COLOR)
                x += font.getlength(tok["text"])
            x += space_w
        y += line_height

    return img


def add_text_overlay(local_in, local_out, width, height, top_original, bottom_original):
    emoji_cache = {}

    top_lines, top_size = fit_tokens(top_original, width, max_lines=2)
    bottom_lines, bottom_size = fit_tokens(bottom_original, width, max_lines=1)

    top_img = render_caption_image(top_lines, top_size, emoji_cache)
    bottom_img = render_caption_image(bottom_lines, bottom_size, emoji_cache)

    top_path = "top_overlay.png"
    bottom_path = "bottom_overlay.png"
    top_img.save(top_path)
    bottom_img.save(bottom_path)

    top_y = int(height * TOP_TEXT_Y_FRACTION)
    bottom_y = int(height * BOTTOM_TEXT_Y_FRACTION)

    filter_complex = (
        f"[0:v][1:v]overlay=(W-w)/2:{top_y}[tmp1];"
        f"[tmp1][2:v]overlay=(W-w)/2:{bottom_y}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", local_in,
        "-i", top_path,
        "-i", bottom_path,
        "-filter_complex", filter_complex,
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
