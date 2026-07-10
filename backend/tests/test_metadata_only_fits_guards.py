"""Metadata-only FITS headers must never masquerade as science products."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from astropy.io import fits
from fastapi import HTTPException


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_name", "class_name"),
    [
        ("app.connectors.alma", "ALMAConnector"),
        ("app.connectors.eso", "ESOConnector"),
        ("app.connectors.irsa", "IRSAConnector"),
        ("app.connectors.jwst", "JWSTConnector"),
        ("app.connectors.lamost", "LAMOSTConnector"),
    ],
)
async def test_metadata_only_connectors_refuse_fake_fits(
    module_name: str, class_name: str
) -> None:
    module = __import__(module_name, fromlist=[class_name])
    connector = getattr(module, class_name)()

    with pytest.raises(NotImplementedError, match="will not fabricate"):
        await connector.fetch("observation-id")


@pytest.mark.asyncio
async def test_fetch_api_reports_unavailable_real_product_without_storing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import data as data_api

    class MetadataOnlyConnector:
        async def fetch(self, _object_id: str):
            raise NotImplementedError("real staged product required")

    monkeypatch.setattr(data_api, "get_connector", lambda _source: MetadataOnlyConnector())

    with pytest.raises(HTTPException) as caught:
        await data_api.fetch_object(
            "archive", "observation-id", db=SimpleNamespace(), user=None
        )
    assert caught.value.status_code == 422
    assert caught.value.detail == "real staged product required"


def test_load_data_rejects_header_only_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline.nodes import load_data as load_data_module

    payload = io.BytesIO()
    fits.PrimaryHDU().writeto(payload)
    monkeypatch.setattr(
        load_data_module, "download_fits", lambda _path: payload.getvalue()
    )

    with pytest.raises(ValueError, match="Metadata-only FITS headers"):
        load_data_module.load_data(None, {"fits_path": "owned/header-only.fits"})
