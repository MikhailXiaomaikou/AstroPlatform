FROM python:3.11-slim@sha256:e031123e3d85762b141ad1cbc56452ba69c6e722ebf2f042cc0dc86c47c0d8b3

WORKDIR /app

# Install system dependencies
# - gcc / gfortran: scipy / camb Fortran extensions
# - libpq-dev: psycopg2-binary backend
# - postgresql-client: pre-deploy migrations and portable backup/restore tools
# - libgsl-dev / libfftw3-dev / liblapack-dev / libblas-dev / make / g++ / wget:
#   PART AI Phase 5 #2 Track 2 — cobaya/camb external_cobaya backend.
#   CAMB compiles its Fortran kernel during pip install, and cobaya-install
#   later pulls extra likelihood data from the network into
#   COBAYA_PACKAGES_PATH (lazy, on first external_cobaya run).
# - fontconfig / fonts-noto-cjk: matplotlib CJK rendering for paper figures
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    postgresql-client \
    gfortran \
    libgsl-dev \
    libfftw3-dev \
    liblapack-dev \
    libblas-dev \
    make \
    wget \
    fontconfig \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# Optional: Install Java for STILTS cross-match backend.
# Uncomment the line below and set STILTS_JAR_PATH env var to enable.
# RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jre-headless && rm -rf /var/lib/apt/lists/*
# ENV STILTS_JAR_PATH=/app/stilts.jar

COPY requirements.txt requirements.lock ./
# Refuse dependency drift or an artifact whose digest is absent from the lock.
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .

# Create data directories
# /app/cobaya_packages: external_cobaya backend's lazy-install target
# (cobaya-install writes likelihood data tables / Planck plik clik etc here).
RUN mkdir -p /app/data/fits /app/data /app/cobaya_packages

# Create non-root user and set ownership
RUN addgroup --system app && adduser --system --ingroup app app
RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
