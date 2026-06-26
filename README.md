# Hệ thống nhận dạng phong cách thời trang

Hệ thống cho phép người dùng upload ảnh trang phục, AI phân tích từng chi tiết thời trang, xác định phong cách tổng thể và gợi ý dịp phù hợp.

## Chức năng

- **Hai cách đăng nhập độc lập:**
  1. **Tài khoản thường:** Đăng ký (email + OTP) → Đăng nhập → dùng app, xem lịch sử.
  2. **Google:** Bấm "Đăng nhập bằng Google" (không cần đăng ký) → dùng app, xem lịch sử.
- Hai luồng không gộp: tài khoản thường và tài khoản Google là hai user khác nhau.
- **Đa ngôn ngữ VI / EN** — dropdown trên topbar; dịch nội dung động qua `/api/translate`
- **Hồ sơ cá nhân** — avatar, họ tên, đổi mật khẩu, xóa tài khoản (grace period)
- **Upload ảnh** — 1 hoặc nhiều ảnh (PNG, JPG, WEBP), giới hạn theo `MAX_ANALYZE_IMAGES` trong `.env` (mặc định 5)
- **Phân tích chi tiết** — Gán category, danh mục món, thuộc tính, phong cách từng món; **Lý do** hiển thị tiếng Việt
- **Phong cách tổng thể** — Ensemble nhiều model vision → gộp món và bình chọn phong cách (có trọng số), rồi tổng hợp outfit có trọng số category
- **Gợi ý dịp** — Map phong cách → dịp phù hợp (Casual, Formal, Streetwear…)
- **Gợi ý mix đồ** — API riêng (`/api/mix-suggestions`), dùng model text trong `.env` (`OPENROUTER_MODEL`)
- **Lịch sử / thống kê / gói cước** — Theo user đã đăng nhập; thống kê có so sánh kỳ, KPI độ tin cậy AI, **xuất báo cáo JSON/PDF**
- **Admin** — Xem chi tiết model phân tích, lưu báo cáo JSON kết quả; trang Quản trị
- **Thanh toán** — VietQR + đối soát SePay (tuỳ cấu hình `.env`)

## Công nghệ

| Tầng | Công nghệ |
|------|-----------|
| **Frontend** | HTML, CSS, JavaScript (`static/`, `templates/`) |
| **Backend** | Python 3, **Flask**, **Flask-CORS** |
| **Auth** | **Authlib** (Google OAuth), **PyJWT**, **Werkzeug** (hash mật khẩu) |
| **Cơ sở dữ liệu** | **SQLite** (dev, `DB_ENGINE=sqlite`) hoặc **MySQL** (`mysql-connector-python`) |
| **i18n / dịch** | `static/data/i18n.json`, `translate_service.py`, `/api/translate` |
| **Cấu hình** | **python-dotenv** (`.env`) |
| **AI** | **OpenRouter** — gọi qua **`openai`** SDK (`base_url` OpenRouter): nhiều model **vision** cho phân tích ảnh; một model **chat** cho gợi ý mix |

> Không dùng SQLAlchemy trong `requirements.txt`. JWT ký bằng **`FLASK_SECRET_KEY`** (`config.SECRET_KEY`).

## Cài đặt

### 1. Môi trường ảo và dependency

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Cấu hình

```bash
copy .env.example .env
```

Trong `.env` (quan trọng):

- **`FLASK_SECRET_KEY`** — Bí mật Flask và ký JWT
- **`JWT_EXPIRES_DAYS`** — Tuỳ chọn (mặc định xem `config.py`)
- **MySQL:** `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`
- **OpenRouter**
  - `OPENROUTER_API_KEY` — Bắt buộc nếu chạy phân tích thật (dạng `sk-or-v1-...`). Không có key → backend trả **kết quả demo**
  - **`OPENROUTER_VISION_MODELS`** — Danh sách model **vision** (phân tích ảnh, thường 3 model, cách nhau dấu phẩy)
  - **`OPENROUTER_MODEL`** — Model **text** cho **gợi ý mix** và dịch (mặc định)
  - **`OPENROUTER_TRANSLATE_MODEL`** — Tuỳ chọn; model riêng cho `/api/translate`
- **SMTP** (tuỳ chọn): OTP đăng ký / quên mật khẩu — `SMTP_*` trong `.env.example`
- **Google OAuth** (tuỳ chọn): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, redirect `http://localhost:5000/api/auth/google/callback`
- **Thanh toán** (tuỳ chọn): VietQR + SePay — xem comment trong `.env.example`

### 3. Database

Chạy **một lần** (tạo DB / bảng):

```bash
mysql -u root -p < scripts/init_mysql.sql
```

Schema đầy đủ nằm trong `scripts/init_mysql.sql` (MySQL) và `scripts/init_sqlite.sql` (SQLite tham khảo). SQLite được app tự tạo khi `DB_ENGINE=sqlite`.

### 4. Chạy ứng dụng

```bash
python app.py
```

Mở trình duyệt: **http://localhost:5000**

## API chính

| Method | Endpoint | Mô tả |
|--------|----------|--------|
| POST | `/api/register`, `/api/register/verify` | Đăng ký + OTP email |
| POST | `/api/login` | Đăng nhập (trả JWT trong body hoặc theo flow frontend) |
| POST | `/api/translate` | Dịch nội dung động (VI ↔ EN) |
| GET | `/api/auth/google` | Bắt đầu đăng nhập Google |
| GET | `/api/auth/google/callback` | OAuth callback → redirect kèm `?token=` JWT |
| GET | `/api/auth/me` | User hiện tại (`Authorization: Bearer <JWT>`) |
| POST | `/api/analyze` | Upload ảnh + phân tích (`user_id` + file, cần đăng nhập) |
| POST | `/api/mix-suggestions` | Gợi ý mix đồ (JSON body) |
| GET | `/api/history/<user_id>` | Lịch sử của user đó |
| GET | `/api/stats/<user_id>?period=all\|7d\|30d` | Thống kê cá nhân |

Còn nhiều route **admin**, **payment**, **profile**, **packages** — xem `app.py`.

## Luồng xử lý phân tích (tóm tắt)

1. Người dùng gửi ảnh → lưu `uploads/`, kiểm tra lượt phân tích (trừ user thường).
2. Mỗi ảnh: gọi **song song** các model trong `OPENROUTER_VISION_MODELS` → mỗi model trả danh sách món + phong cách/gợi ý tin cậy.
3. **Gộp món và bình chọn phong cách** trong code (`ai_service`), có ràng buộc từ `fashion_style_dataset.json`.
4. Tổng hợp **phong cách outfit** (`aggregate_styles`) → dịp, mô tả; trường `overall_style_top_k` (Top-3) trong JSON response.
5. Lưu `history` trong MySQL (nếu có user) và trả JSON cho frontend.

## Kiểm chứng độ chính xác (tuỳ chọn)

Ground truth lấy từ file Excel `Benchmark Anh AI.xlsx` (cột Image_ID, Overall_Style, Item, Item_Style, Image).

```bash
# Chuyển Excel → ground_truth_parsed.jsonl + ảnh trong media/
python scripts/excel_benchmark_to_ground_truth.py --xlsx "Benchmark Anh AI.xlsx" --out-dir artifacts/eval_excel

# Chạy eval (server Flask + user trong DB phải sẵn sàng)
python eval_fashion_accuracy.py --gt artifacts/eval_excel/ground_truth_parsed.jsonl --media artifacts/eval_excel/media --api http://127.0.0.1:5000 --user-id 1 --out artifacts/eval_excel/results
```

Chi tiết metric xem docstring trong `eval_fashion_accuracy.py`. Kết quả trong `artifacts/eval_excel/results/` — gồm **`report.xlsx`** (sheet Chi tiết + Tổng hợp), `report.csv`, `summary.json` (nên không commit — thư mục `artifacts/` đã có trong `.gitignore`).

## Cấu trúc thư mục (thiết yếu)

```
Nhan_dang_thoi_trang/
├── app.py              # Flask, toàn bộ route & nghiệp vụ HTTP
├── config.py           # Cấu hình từ .env
├── ai_service.py       # OpenRouter ensemble, dataset, aggregate, occasions, mix prompt
├── translate_service.py
├── auth_handlers.py / auth_otp.py / account_deletion.py / email_service.py
├── eval_fashion_accuracy.py   # Script đánh giá (gọi /api/analyze)
├── requirements.txt
├── .env.example
├── data/
│   └── fashion_style_dataset.json
├── static/
│   ├── data/i18n.json
│   ├── css/            # shell-v2, stats-v2, profile-v2, …
│   └── js/             # app.js, i18n.js, auth.js, …
├── scripts/            # SQL migration, tiện ích
├── templates/          # Jinja/HTML trang chủ, đăng nhập, admin…
├── docs/               # Hướng dẫn cài đặt, sử dụng, báo cáo kỹ thuật
└── uploads/            # Ảnh upload (git chỉ giữ .gitkeep)
```

## Ghi chú

- **OpenRouter:** Model vision đổi trong `OPENROUTER_VISION_MODELS`; `OPENROUTER_MODEL` / `OPENROUTER_TRANSLATE_MODEL` cho mix và dịch.
- **Nhiều ảnh:** Phân tích song song trong một request (giới hạn `MAX_ANALYZE_IMAGES`).
- **Tài liệu chi tiết:** xem thư mục [`docs/`](docs/) — cài đặt, sử dụng, công nghệ, báo cáo thiết kế giải thuật.
