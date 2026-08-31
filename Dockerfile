FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV ALLOW_PROXY=1
ENV DMCA_DB_PATH=/app/data/dmca_monitor.db

EXPOSE 3000

CMD ["python3", "-c", "from src.server import create_app; app=create_app(); app.run(host='0.0.0.0', port=3000, debug=False)"]
