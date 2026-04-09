FROM python:3.11-slim

# ── System dependencies + Chromium ──────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libexpat1 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libxkbcommon0 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# ── Chrome / Chromedriver paths ──────────────────────────────────
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_BIN=/usr/bin/chromedriver

# ── Python app ──────────────────────────────────────────────────
WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# ── Runtime config ──────────────────────────────────────────────
# Use /app/instance for SQLite — já tem permissão de escrita
RUN mkdir -p /app/instance

# Non-root user for security
RUN useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

# Use waitress (production WSGI) instead of Flask dev server
CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "--threads=4", "app:app"]
