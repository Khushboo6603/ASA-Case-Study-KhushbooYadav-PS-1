FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt && mkdir -p uploads
ENV PORT=8080
CMD gunicorn app:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT
