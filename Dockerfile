# Dockerfile - works on Railway, Render, Fly.io, AWS, Poridhi, or any host.
# Image size constraint: <500MB recommended, <1GB hard limit per hackathon rules.
# Runtime constraints: no GPU, no multi-GB downloads, no runtime training.

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/

# Expose the port the app binds to. Render/Railway inject $PORT; default 8000.
EXPOSE 8000

# Bind to 0.0.0.0 - REQUIRED per hackathon docker rules.
# Use shell form so $PORT is expanded at runtime.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
