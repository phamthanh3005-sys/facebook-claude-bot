"""
youtube_handler.py
------------------
Kết nối YouTube Data API v3:
- Poll bình luận mới trên các video của kênh
- Tự động trả lời bằng Claude
- Nhận thông báo video mới qua PubSubHubbub (WebSub)

Yêu cầu env:
  YOUTUBE_CLIENT_ID        — OAuth 2.0 client ID
  YOUTUBE_CLIENT_SECRET    — OAuth 2.0 client secret
  YOUTUBE_REFRESH_TOKEN    — OAuth 2.0 refresh token (lấy 1 lần)
  YOUTUBE_CHANNEL_ID       — ID kênh YouTube cần theo dõi
  YOUTUBE_POLL_INTERVAL    — Khoảng cách poll (giây), mặc định 300
"""

import os
import json
import asyncio
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from claude_handler import ask_claude

YOUTUBE_CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN")
YOUTUBE_CHANNEL_ID    = os.getenv("YOUTUBE_CHANNEL_ID")
YOUTUBE_POLL_INTERVAL = int(os.getenv("YOUTUBE_POLL_INTERVAL", "300"))

REPLIED_FILE = Path("replied_youtube_comments.json")


def _load_replied() -> set:
    if REPLIED_FILE.exists():
        try:
            return set(json.loads(REPLIED_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_replied(replied: set):
    items = list(replied)[-20_000:]
    REPLIED_FILE.write_text(json.dumps(items))


def _build_youtube_service():
    """Trả về authenticated YouTube service object."""
    creds = Credentials(
        token=None,
        refresh_token=YOUTUBE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/youtube.force-ssl"],
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


async def poll_and_reply_comments():
    """
    Kiểm tra bình luận mới trên 10 video gần nhất của kênh.
    Bình luận chưa được trả lời → gọi Claude → reply.
    """
    if not all([YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET,
                YOUTUBE_REFRESH_TOKEN, YOUTUBE_CHANNEL_ID]):
        print("[YouTube] Thiếu credentials — bỏ qua poll.")
        return

    print("[YouTube] Bắt đầu poll bình luận...")
    try:
        youtube = _build_youtube_service()
        replied = _load_replied()

        search_resp = youtube.search().list(
            channelId=YOUTUBE_CHANNEL_ID,
            part="id",
            order="date",
            maxResults=10,
            type="video",
        ).execute()

        for item in search_resp.get("items", []):
            video_id = item["id"]["videoId"]
            await _process_video_comments(youtube, video_id, replied)

    except Exception as exc:
        print(f"[YouTube Poll Error] {exc}")


async def _process_video_comments(youtube, video_id: str, replied: set):
    """Xử lý bình luận top-level của một video."""
    try:
        threads_resp = youtube.commentThreads().list(
            videoId=video_id,
            part="snippet",
            order="time",
            maxResults=20,
            moderationStatus="published",
        ).execute()
    except HttpError as exc:
        print(f"[YouTube] Không thể lấy comment video {video_id}: {exc.reason}")
        return

    for thread in threads_resp.get("items", []):
        thread_id = thread["id"]
        if thread_id in replied:
            continue

        top    = thread["snippet"]["topLevelComment"]["snippet"]
        author = top.get("authorDisplayName", "bạn")
        text   = top.get("textDisplay", "").strip()

        if not text:
            continue

        print(f"[YouTube] Comment mới từ {author} (video {video_id}): {text[:100]}")

        context = (
            f"Đây là bình luận trên video YouTube của kênh từ người dùng {author}. "
            "Hãy trả lời thân thiện, ngắn gọn và liên quan đến nội dung kênh/video."
        )

        loop = asyncio.get_event_loop()
        reply_text = await loop.run_in_executor(
            None, lambda: ask_claude(text, context=context)
        )

        await _reply_to_comment(youtube, thread_id, reply_text)
        replied.add(thread_id)

    _save_replied(replied)


async def _reply_to_comment(youtube, parent_id: str, text: str):
    """Gửi reply vào một comment thread."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: youtube.comments().insert(
                part="snippet",
                body={
                    "snippet": {
                        "parentId": parent_id,
                        "textOriginal": text,
                    }
                },
            ).execute(),
        )
        print(f"[YouTube] Đã reply comment {parent_id}")
    except HttpError as exc:
        print(f"[YouTube Reply Error] {parent_id}: {exc.reason}")


async def start_youtube_polling():
    """
    Vòng lặp vô hạn: poll định kỳ theo YOUTUBE_POLL_INTERVAL.
    Gọi hàm này khi khởi động server (FastAPI lifespan).
    """
    while True:
        await poll_and_reply_comments()
        await asyncio.sleep(YOUTUBE_POLL_INTERVAL)


def parse_new_video_notification(xml_body: bytes) -> dict | None:
    """
    Parse XML từ PubSubHubbub khi kênh đăng video mới.
    Trả về dict {'video_id', 'title', 'channel_id'} hoặc None.
    """
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_body)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt":   "http://www.youtube.com/xml/schemas/2015",
        }
        entry = root.find("atom:entry", ns)
        if entry is None:
            return None

        video_id   = entry.findtext("yt:videoId", namespaces=ns)
        title      = entry.findtext("atom:title", namespaces=ns)
        channel_id = entry.findtext("yt:channelId", namespaces=ns)

        if video_id:
            return {"video_id": video_id, "title": title, "channel_id": channel_id}
    except Exception as exc:
        print(f"[YouTube WebSub Parse Error] {exc}")
    return None
