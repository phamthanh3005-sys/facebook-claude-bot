"""
claude_handler.py
-----------------
Xử lý tất cả logic gọi Claude API.
Dùng prompt caching để tiết kiệm token khi system prompt dài.
"""

import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

# Khởi tạo client một lần duy nhất (tái sử dụng connection)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ============================================================
# SYSTEM PROMPT — Tùy chỉnh theo business của bạn
# Prompt này được CACHE → chỉ tính tiền 1 lần mỗi 5 phút
# ============================================================
SYSTEM_PROMPT = """Bạn là trợ lý AI của Fanpage [TÊN FANPAGE].

NHIỆM VỤ:
- Trả lời tin nhắn và bình luận của khách hàng trên Facebook
- Tư vấn về sản phẩm/dịch vụ một cách thân thiện, chuyên nghiệp
- Hướng dẫn khách hàng đặt hàng hoặc liên hệ tư vấn trực tiếp

THÔNG TIN VỀ FANPAGE:
- Lĩnh vực: [Điền lĩnh vực kinh doanh của bạn]
- Sản phẩm/dịch vụ chính: [Mô tả sản phẩm]
- Giờ làm việc: 8:00 - 22:00 (Thứ 2 - Chủ nhật)
- Hotline: [Số điện thoại]
- Website: [URL nếu có]

QUY TẮC TRẢ LỜI:
1. Luôn xưng hô thân thiện (em/anh/chị phù hợp với ngữ cảnh)
2. Trả lời ngắn gọn, súc tích — không quá 200 từ
3. Nếu không biết → thành thật và đề nghị khách để lại SĐT để tư vấn
4. KHÔNG hứa hẹn giá hoặc thời gian giao hàng cụ thể nếu chưa chắc chắn
5. Kết thúc bằng lời mời hành động (CTA) phù hợp

PHONG CÁCH: Thân thiện, chuyên nghiệp, nhanh nhẹn."""


def ask_claude(user_message: str, context: str = "") -> str:
    """
    Gọi Claude API với prompt caching cho system prompt.

    Args:
        user_message: Tin nhắn/bình luận từ người dùng Facebook
        context: Thông tin thêm (tên người dùng, loại tương tác, v.v.)

    Returns:
        Chuỗi phản hồi từ Claude
    """

    # Ghép context vào message nếu có
    full_message = f"{context}\n\nKhách hỏi: {user_message}" if context else user_message

    try:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=500,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # Cache system prompt → tiết kiệm ~90% chi phí cho prompt dài
                    "cache_control": {"type": "ephemeral"}
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": full_message
                }
            ]
        )

        # Lấy text từ response
        for block in response.content:
            if block.type == "text":
                return block.text

        return "Xin lỗi, em không thể xử lý tin nhắn này. Vui lòng liên hệ hotline để được hỗ trợ."

    except anthropic.APIConnectionError:
        return "Em đang gặp sự cố kết nối. Anh/chị vui lòng nhắn lại sau nhé!"
    except anthropic.RateLimitError:
        return "Hệ thống đang bận. Vui lòng thử lại sau ít phút!"
    except anthropic.APIStatusError as e:
        print(f"[Claude API Error] {e.status_code}: {e.message}")
        return "Xin lỗi, em không thể trả lời ngay lúc này. Vui lòng liên hệ hotline!"
