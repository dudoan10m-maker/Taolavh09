TOOLMOWIS BACKEND

API URL mới:
os.getenv("PUBLIC_API_URL", "https://taolavh09-1.onrender.com").rstrip("/")

Render Start Command:
gunicorn app:app

Biến môi trường bắt buộc để lưu vĩnh viễn:
DATABASE_URL=<PostgreSQL Internal Database URL>

Nếu admin/frontend có biến API_BASE thì đặt:
https://taolavh09-1.onrender.com
