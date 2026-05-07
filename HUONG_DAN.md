# Hướng Dẫn Cài Đặt & Chạy Facebook Claude Bot

## BƯỚC 1 — Cài đặt môi trường

```bash
# Tạo virtual environment (khuyến nghị)
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Mac/Linux

# Cài thư viện
pip install -r requirements.txt
```

## BƯỚC 2 — Điền thông tin vào .env

Mở file `.env` và điền:
```
PAGE_ACCESS_TOKEN=EAAB...xyz   ← Lấy từ Facebook Developers > Messenger > Access Tokens
VERIFY_TOKEN=chuoi_bi_mat_ban_tu_dat   ← Tự đặt, ghi nhớ để dùng khi cấu hình webhook
ANTHROPIC_API_KEY=sk-ant-...   ← Lấy từ console.anthropic.com
```

## BƯỚC 3 — Chạy server

```bash
python main.py
# Hoặc:
uvicorn main:app --reload --port 8000
```

Mở trình duyệt → http://localhost:8000 → Thấy `{"status": "running"}` là OK.

## BƯỚC 4 — Expose server ra internet (bắt buộc cho Facebook Webhook)

Facebook cần gọi được URL của bạn từ internet. Dùng **ngrok** (miễn phí):

```bash
# Tải ngrok tại: https://ngrok.com/download
# Sau khi cài:
ngrok http 8000
```

Ngrok sẽ hiện URL dạng: `https://abc123.ngrok-free.app`

→ Copy URL này, thêm `/webhook` phía sau:
`https://abc123.ngrok-free.app/webhook`

## BƯỚC 5 — Đăng ký Webhook với Facebook

1. Vào https://developers.facebook.com
2. Chọn App của bạn → **Messenger → Settings**
3. **Webhooks → Add Callback URL:**
   - Callback URL: `https://abc123.ngrok-free.app/webhook`
   - Verify Token: (điền đúng cái bạn đặt trong .env)
4. Click **Verify and Save** ← Facebook sẽ gọi GET /webhook để xác minh
5. Subscribe fields: tích `messages` và `messaging_postbacks`
6. Thêm Page vào subscription

## BƯỚC 6 — Test

Gửi tin nhắn đến Fanpage → Server nhận → Claude trả lời → Facebook hiển thị

---

## Triển Khai Thực Tế (Production)

Thay ngrok bằng VPS thật. Các lựa chọn rẻ/miễn phí:

| Nền tảng | Giá | Ghi chú |
|---|---|---|
| **Railway.app** | Free tier | Dễ nhất — push code là chạy |
| **Render.com** | Free tier | Sleep sau 15 phút không dùng |
| **VPS Vultr/DigitalOcean** | ~$6/tháng | Ổn định nhất |
| **AWS EC2** | Free 1 năm | Cần cấu hình nhiều hơn |

### Deploy lên Railway (nhanh nhất):
```bash
# 1. Tạo tài khoản tại railway.app
# 2. Cài Railway CLI:
npm install -g @railway/cli

# 3. Login và deploy:
railway login
railway init
railway up
```

---

## Tùy Chỉnh Claude System Prompt

Mở file `claude_handler.py` → Sửa biến `SYSTEM_PROMPT`:

```python
SYSTEM_PROMPT = """Bạn là trợ lý AI của [TÊN FANPAGE].

THÔNG TIN:
- Sản phẩm: [Mô tả sản phẩm của bạn]
- Giá: [Bảng giá nếu muốn Claude biết]
...
"""
```

System prompt này được **cache tự động** → chỉ tính tiền token 1 lần mỗi 5 phút,
dù có 1000 khách nhắn tin cùng lúc.

---

## Cấu Trúc Luồng Dữ Liệu

```
Khách gửi tin/comment
        ↓
Facebook Graph API
        ↓
POST /webhook (server của bạn)
        ↓ (background task — không chặn response)
claude_handler.ask_claude()
        ↓
Anthropic API → Claude Opus 4.7
        ↓
send_message() hoặc reply_comment()
        ↓
Graph API → Facebook → Khách nhận phản hồi
```

Toàn bộ luồng mất khoảng 2-5 giây.
