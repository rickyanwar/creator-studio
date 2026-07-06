FROM python:3.11-slim

WORKDIR /app

# System deps for instagrapi + psycopg2 + fonts-dejavu-core (bold font for
# deterministic text-overlay rendering in ai_image_edit.py)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    curl \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create media directory (will be overridden by volume on VPS)
RUN mkdir -p /var/www/media/posts

EXPOSE 8000
