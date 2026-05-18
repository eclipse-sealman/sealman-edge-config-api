FROM python:3.14.4-slim-trixie

# Keeps Python from generating .pyc files and forces stdout/stderr to be unbuffered
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

ARG VERSION
ENV VERSION=${VERSION}

# Install dependencies first (better layer caching, no pip cache in image)
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY --chmod=555 --chown=nobody:nogroup . /app

# Create logs directory with correct ownership for the nobody user
RUN mkdir -p /app/logs && chown -R nobody:nogroup /app/logs

USER nobody

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-5000}"]