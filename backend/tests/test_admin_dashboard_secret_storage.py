from pathlib import Path


_DASHBOARD = (
    Path(__file__).resolve().parents[1] / "app" / "static" / "astro_admin.html"
)


def test_admin_secret_is_memory_only_and_legacy_storage_is_purged() -> None:
    html = _DASHBOARD.read_text(encoding="utf-8")

    assert "localStorage.setItem(LS_SECRET" not in html
    assert "localStorage.getItem(LS_SECRET" not in html
    assert "sessionStorage.setItem" not in html
    assert "localStorage.removeItem(LEGACY_LS_SECRET)" in html
    assert "secret: ''" in html
