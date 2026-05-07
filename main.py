"""
main.py
-------
Server FastAPI nhận webhook từ Facebook Messenger & Comments
Luong: Facebook Event - Verify - Goi Claude - Reply via Graph API
"""

import os
import httpx
import hashlib
import hmac
import json
import asyncio
from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from claude_handler import ask_claude

load_dotenv()

app = FastAPI(title="Facebook Claude Bot")

# ── Biến môi trường ──────────────────────────────────────────
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")
VERIFY_TOKEN      = os.getenv("VERIFY_TOKEN")
FB_API_VERSION    = os.getenv("FB_API_VERSION", "v21.0")
FB_GRAPH_URL      = f"https://graph.facebook.com/{FB_API_VERSION}"


# ════════════════════════════════════════════════════════════
# BƯỚC 1: WEBHOOK VERIFICATION (Facebook gọi GET để xác minh)
# ════════════════════════════════════════════════════════════
@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Facebook gửi GET request để xác minh webhook.
    Phải trả về hub.challenge nếu hub.verify_token khớp.
    """
    params = dict(request.query_params)
    mode         = params.get("hub.mode")
    token        = params.get("hub.verify_token")
    challenge    = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("[Webhook] OK - Verified successfully")
        return Response(content=challenge, media_type="text/plain")
    else:
        print(f"[Webhook] FAILED - token mismatch: {token}")
        raise HTTPException(status_code=403, detail="Verification failed")


# ════════════════════════════════════════════════════════════
# BƯỚC 2: NHẬN SỰ KIỆN TỪ FACEBOOK (POST)
# ════════════════════════════════════════════════════════════
@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Facebook gửi POST khi có tin nhắn mới hoặc bình luận mới.
    Trả về 200 ngay lập tức (Facebook timeout sau 5 giây),
    xử lý thực tế chạy ngầm.
    """
    body = await request.json()

    print(f"[Webhook] Received: {json.dumps(body, indent=2, ensure_ascii=False)[:500]}")

    if body.get("object") == "page":
        for entry in body.get("entry", []):

            # ── TIN NHẮN RIÊNG (Messenger) ──────────────────
            for messaging in entry.get("messaging", []):
                if "message" in messaging:
                    background_tasks.add_task(handle_message, messaging)

            # ── BÌNH LUẬN (Comments trên bài đăng) ──────────
            for change in entry.get("changes", []):
                if change.get("field") == "feed":
                    value = change.get("value", {})
                    if value.get("item") == "comment" and value.get("verb") == "add":
                        background_tasks.add_task(handle_comment, value)

    # Tra ve 200 ngay - bat buoc de Facebook khong retry
    return {"status": "ok"}


# ════════════════════════════════════════════════════════════
# XỬ LÝ TIN NHẮN MESSENGER
# ════════════════════════════════════════════════════════════
async def handle_message(messaging: dict):
    """Xử lý tin nhắn riêng từ Messenger."""

    sender_id = messaging["sender"]["id"]
    message   = messaging.get("message", {})

    # Bỏ qua echo message (tin nhắn do page tự gửi)
    if message.get("is_echo"):
        return

    text = message.get("text", "")
    if not text:
        return  # Bỏ qua sticker/file/voice

    print(f"[Message] From {sender_id}: {text}")

    # Lấy tên người dùng để trả lời thân thiện hơn
    user_name = await get_user_name(sender_id)
    context = f"Đang trò chuyện với: {user_name}" if user_name else ""

    # Gọi Claude
    reply_text = ask_claude(text, context=context)

    # Gửi phản hồi
    await send_message(sender_id, reply_text)


# ════════════════════════════════════════════════════════════
# XỬ LÝ BÌNH LUẬN TRÊN BÀI ĐĂNG
# ════════════════════════════════════════════════════════════
async def handle_comment(value: dict):
    """Xử lý bình luận mới trên bài đăng của page."""

    comment_id  = value.get("comment_id") or value.get("id")
    from_info   = value.get("from", {})
    sender_name = from_info.get("name", "bạn")
    text        = value.get("message", "")

    if not text or not comment_id:
        return

    # Bỏ qua bình luận từ chính Page (tránh loop)
    if value.get("from", {}).get("id") == value.get("post", {}).get("id"):
        return

    print(f"[Comment] From {sender_name}: {text}")

    context = f"Đây là bình luận trên bài đăng Facebook từ {sender_name}"

    # Gọi Claude
    reply_text = ask_claude(text, context=context)

    # Reply vào comment
    await reply_comment(comment_id, reply_text)


# ════════════════════════════════════════════════════════════
# GỬI TIN NHẮN QUA GRAPH API
# ════════════════════════════════════════════════════════════
async def send_message(recipient_id: str, text: str):
    """Gửi tin nhắn riêng qua Messenger Send API."""

    url = f"{FB_GRAPH_URL}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
        "messaging_type": "RESPONSE"
    }
    params = {"access_token": PAGE_ACCESS_TOKEN}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, params=params)

    if response.status_code != 200:
        print(f"[Send Error] {response.status_code}: {response.text}")
    else:
        print(f"[Send OK] Message sent to {recipient_id}")


async def reply_comment(comment_id: str, text: str):
    """Reply vào một comment cụ thể."""

    url = f"{FB_GRAPH_URL}/{comment_id}/comments"
    payload = {"message": text}
    params  = {"access_token": PAGE_ACCESS_TOKEN}

    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, params=params)

    if response.status_code != 200:
        print(f"[Comment Reply Error] {response.status_code}: {response.text}")
    else:
        print(f"[Comment Reply OK] Replied to comment {comment_id}")


async def get_user_name(user_id: str) -> str:
    """Lấy tên người dùng từ Graph API (để trả lời thân thiện hơn)."""
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


# ════════════════════════════════════════════════════════════
# HEALTH CHECK
# ════════════════════════════════════════════════════════════
@app.get("/")
async def health_check():
    return {
        "status": "running",
        "service": "Facebook Claude Bot",
        "endpoints": {
            "webhook_verify": "GET /webhook",
            "webhook_receive": "POST /webhook"
        }
    }


# ════════════════════════════════════════════════════════════
# CHẠY SERVER
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"[Server] Starting on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
