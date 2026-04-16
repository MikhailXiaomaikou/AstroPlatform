FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    gfortran \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# Optional: Install Java for STILTS cross-match backend.
# Uncomment the line below and set STILTS_JAR_PATH env var to enable.
# RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless && rm -rf /var/lib/apt/lists/*
# ENV STILTS_JAR_PATH=/app/stilts.jar

COPY requirements.txt .
# v3: install with fallback, gfortran available for scipy
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories
RUN mkdir -p /app/data/fits /app/data

# Create non-root user and set ownership
RUN addgroup --system app && adduser --system --ingroup app app
RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
