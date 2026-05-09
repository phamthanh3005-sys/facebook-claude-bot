"""
main.py
-------
Server FastAPI nhận webhook từ Facebook Messenger & Comments
và tự động trả lời bình luận YouTube qua polling + WebSub.
Luồng: Event → Verify → Gọi Claude → Reply via Graph API / YouTube API
"""

import os
import httpx
import json
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from claude_handler import ask_claude
from youtube_handler import start_youtube_polling, parse_new_video_notification

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(start_youtube_polling())
    yield
    task.cancel()


app = FastAPI(title="Facebook Claude Bot", lifespan=lifespan)

PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN      = os.getenv("VERIFY_TOKEN")
FB_API_VERSION    = os.getenv("FB_API_VERSION", "v21.0")
FB_GRAPH_URL      = f"https://graph.facebook.com/{FB_API_VERSION}"


@app.get("/webhook")
async def verify_webhook(request: Request):
    params    = dict(request.query_params)
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("[Webhook] OK - Verified successfully")
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    print(f"[Webhook] Received: {json.dumps(body, indent=2, ensure_ascii=False)[:500]}")

    if body.get("object") == "page":
        for entry in body.get("entry", []):
            for messaging in entry.get("messaging", []):
                if "message" in messaging:
                    background_tasks.add_task(handle_message, messaging)
            for change in entry.get("changes", []):
                if change.get("field") == "feed":
                    value = change.get("value", {})
                    if value.get("item") == "comment" and value.get("verb") == "add":
                        background_tasks.add_task(handle_comment, value)

    return {"status": "ok"}


async def handle_message(messaging: dict):
    sender_id = messaging["sender"]["id"]
    message   = messaging.get("message", {})

    if message.get("is_echo"):
        return

    text = message.get("text", "")
    if not text:
        return

    print(f"[Message] From {sender_id}: {text}")
    user_name  = await get_user_name(sender_id)
    context    = f"Đang trò chuyện với: {user_name}" if user_name else ""
    reply_text = ask_claude(text, context=context)
    await send_message(sender_id, reply_text)


async def handle_comment(value: dict):
    comment_id  = value.get("comment_id") or value.get("id")
    from_info   = value.get("from", {})
    sender_name = from_info.get("name", "bạn")
    text        = value.get("message", "")

    if not text or not comment_id:
        return
    if value.get("from", {}).get("id") == value.get("post", {}).get("id"):
        return

    print(f"[Comment] From {sender_name}: {text}")
    context    = f"Đây là bình luận trên bài đăng Facebook từ {sender_name}"
    reply_text = ask_claude(text, context=context)
    await reply_comment(comment_id, reply_text)


async def send_message(recipient_id: str, text: str):
    url     = f"{FB_GRAPH_URL}/me/messages"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}, "messaging_type": "RESPONSE"}
    params  = {"access_token": PAGE_ACCESS_TOKEN}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, params=params)

    if response.status_code != 200:
        print(f"[Send Error] {response.status_code}: {response.text}")
    else:
        print(f"[Send OK] Message sent to {recipient_id}")


async def reply_comment(comment_id: str, text: str):
    url     = f"{FB_GRAPH_URL}/{comment_id}/comments"
    payload = {"message": text}
    params  = {"access_token": PAGE_ACCESS_TOKEN}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, params=params)

    if response.status_code != 200:
        print(f"[Comment Reply Error] {response.status_code}: {response.text}")
    else:
        print(f"[Comment Reply OK] Replied to comment {comment_id}")


async def get_user_name(user_id: str) -> str:
    try:
        url    = f"{FB_GRAPH_URL}/{user_id}"
        params = {"fields": "name", "access_token": PAGE_ACCESS_TOKEN}
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url, params=params)
        if response.status_code == 200:
            return response.json().get("name", "")
    except Exception:
        pass
    return ""


# ── YouTube PubSubHubbub ─────────────────────────────────────

YOUTUBE_VERIFY_TOKEN = os.getenv("YOUTUBE_VERIFY_TOKEN", "youtube_webhook_secret")


@app.get("/youtube/webhook")
async def youtube_websub_verify(request: Request):
    params    = dict(request.query_params)
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if token == YOUTUBE_VERIFY_TOKEN and mode in ("subscribe", "unsubscribe"):
        print(f"[YouTube WebSub] Verified — mode={mode}")
        return Response(content=challenge, media_type="text/plain")

    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/youtube/webhook")
async def youtube_websub_notify(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    info = parse_new_video_notification(body)

    if info:
        print(f"[YouTube] Video mới: id={info['video_id']} title={info['title']}")
    else:
        print("[YouTube WebSub] Nhận notification nhưng không parse được video.")

    return Response(status_code=204)


@app.get("/")
async def health_check():
    return {
        "status": "running",
        "service": "Facebook Claude Bot",
        "endpoints": {
            "webhook_verify":        "GET  /webhook",
            "webhook_receive":       "POST /webhook",
            "youtube_websub_verify": "GET  /youtube/webhook",
            "youtube_websub_notify": "POST /youtube/webhook",
        },
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"[Server] Starting on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
