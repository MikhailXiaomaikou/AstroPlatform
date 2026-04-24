FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
# WeasyPrint (used by the chat-to-PDF export) requires Pango + Cairo +
# HarfBuzz at runtime; without these the import itself succeeds but
# rendering raises OSError.  fonts-noto-cjk covers Chinese glyphs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    gfortran \
    fontconfig \
    fonts-noto-cjk \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libharfbuzz0b \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
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
