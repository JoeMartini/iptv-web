FROM python:3.11-slim

WORKDIR /app

# Install curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r iptv && useradd -r -g iptv -d /app iptv

# Create cache directory with correct ownership
RUN mkdir -p /app/cache && chown iptv:iptv /app/cache

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY --chown=iptv:iptv app.py config.py playlist.m3u ./
COPY --chown=iptv:iptv templates/ ./templates/
COPY --chown=iptv:iptv static/ ./static/

USER iptv

EXPOSE 5005

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:5005/ || exit 1

CMD ["python3", "app.py"]
