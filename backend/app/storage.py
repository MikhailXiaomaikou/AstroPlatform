"""FITS file storage — local filesystem mode (no MinIO required)."""

from pathlib import Path

from app.config import settings

_storage_root = Path(settings.local_storage_dir)


def _ensure_dir(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)


def _validate_path(path: str) -> Path:
    """Validate that path stays within storage root (prevent traversal)."""
    full = (_storage_root / path).resolve()
    root = _storage_root.resolve()
    try:
        full.relative_to(root)
    except ValueError:
        raise ValueError(f"Path traversal detected: {path}")
    return full


def upload_fits(path: str, data: bytes) -> str:
    """Save FITS bytes to local filesystem and return the path."""
    full = _validate_path(path)
    _ensure_dir(full)
    full.write_bytes(data)
    return path


def download_fits(path: str) -> bytes:
    """Read FITS bytes from local filesystem."""
    full = _validate_path(path)
    if not full.exists():
        raise FileNotFoundError(f"FITS file not found: {path}")
    return full.read_bytes()
