"""FITS file storage — local filesystem mode (no MinIO required)."""

from pathlib import Path

from app.config import settings

_storage_root = Path(settings.local_storage_dir)


def _ensure_dir(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def upload_fits(path: str, data: bytes) -> str:
    """Save FITS bytes to local filesystem and return the path."""
    full = _storage_root / path
    _ensure_dir(full)
    full.write_bytes(data)
    return path


def download_fits(path: str) -> bytes:
    """Read FITS bytes from local filesystem."""
    full = _storage_root / path
    if not full.exists():
        raise FileNotFoundError(f"FITS file not found: {path}")
    return full.read_bytes()
