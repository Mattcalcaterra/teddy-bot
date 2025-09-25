FROM python:3.12-slim

# OS deps: FFmpeg for audio playback
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && apt-get install -y git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY src ./src
COPY src/creed.txt /app/creed.txt
COPY src/cookies.txt /app/cookies.txt

# Drop root
RUN useradd -m bot && chown -R bot:bot /app
USER bot

ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=30s --timeout=5s CMD python -m src.healthcheck || exit 1
CMD ["python","-m","src.bot"]
