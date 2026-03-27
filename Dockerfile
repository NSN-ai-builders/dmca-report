FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Allow nginx reverse proxy (bypass localhost-only filter)
ENV ALLOW_PROXY=1

EXPOSE 3000

CMD ["python3", "-c", "\
import json; \
from main import load_settings; \
from src.server import create_app; \
settings = load_settings('config/settings.json'); \
app = create_app(domains_path='config/domains.txt', settings=settings, max_age=3600); \
app.run(host='0.0.0.0', port=3000, debug=False) \
"]
