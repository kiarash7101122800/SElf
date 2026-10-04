FROM python:3.11-slim

# Set workdir
WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for cache
COPY requirements.txt .

# Install python deps
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy all files
COPY . .

# Create required directories
RUN mkdir -p data data/action downloads logs templates && \
    chmod +x start.sh || true

# Expose port (Railway will override with $PORT)
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:${PORT:-8000}/health', timeout=5)" || exit 1

# Run
CMD ["sh", "start.sh"]
