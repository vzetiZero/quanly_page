# Facebook Page Collector & Poster

Project này hỗ trợ:
- Thu thập Business Manager và Pages từ Facebook Graph API v23.0
- Trích xuất Page Access Token cho từng Page
- Fallback từ me/accounts nếu không thấy trong BM
- Xuất kết quả sang TXT và JSON
- Đăng text, ảnh, video lên Page
- Hỗ trợ đăng video theo lịch
- Retry và xử lý rate limit
- Logging chi tiết
- Parallel processing cho việc thu thập Page

## Cài đặt
```bash
pip install -r requirements.txt
```

## Cấu hình environment
Tạo hoặc chỉnh sửa file .env với nội dung:
```env
FACEBOOK_ACCESS_TOKEN=your_user_access_token
FACEBOOK_PAGE_ACCESS_TOKEN=your_page_access_token
FB_API_VERSION=v23.0
REQUEST_TIMEOUT=30
MAX_RETRIES=3
RETRY_DELAY=2
RATE_LIMIT_DELAY=0.5
MAX_WORKERS=3
OUTPUT_DIR=./output
CACHE_DIR=./.cache
```

## Kiểm tra token và quyền Meta App
```bash
python main.py verify-token
```
Lệnh này sẽ kiểm tra token, quyền `pages_show_list`, `business_management`, `pages_manage_posts` và `publish_video`, rồi lưu kết quả vào output/token_verification.json.

## Thu thập danh sách Pages
```bash
python main.py collect
```

Nếu chưa có token thật, có thể dùng dry-run:
```bash
python main.py collect --dry-run
```

## Đăng bài text
```bash
python main.py post-text --page-id YOUR_PAGE_ID --page-token YOUR_PAGE_TOKEN --message "Hello"
```

## Đăng ảnh
```bash
python main.py post-photo --page-id YOUR_PAGE_ID --page-token YOUR_PAGE_TOKEN --image-path ./image.jpg --caption "Caption"
```

## Đăng video
```bash
python main.py post-video --page-id YOUR_PAGE_ID --page-token YOUR_PAGE_TOKEN --video-path ./video.mp4 --title "Title" --description "Desc"
```

## Mẫu code Python
```python
from main import FacebookAPIConfig, PagePostManager

config = FacebookAPIConfig()
manager = PagePostManager(config)
result = manager.post_video(
    page_id="YOUR_PAGE_ID",
    page_token="YOUR_PAGE_TOKEN",
    video_path="video.mp4",
    title="My video",
    description="Posted via Graph API",
)
print(result)
```
