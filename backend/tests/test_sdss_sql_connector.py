"""J3 — SDSS SkyServer direct-connection connector unit tests.

No real network calls; all mocked with httpx.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.connectors.sdss_sql import execute_sdss_sql, _parse_skyserver_json


# ---------- _parse_skyserver_json ----------

def test_parse_skyserver_list_wrapper():
    """SkyServer format=json typically returns [{"Rows": [...]}]."""
    payload = [
        {
            "Rows": [
                {"objID": 1237645877629878395, "ra": 180.0, "dec": 0.0, "r": 18.5},
                {"objID": 1237645877629878396, "ra": 180.1, "dec": 0.1, "r": 19.1},
            ]
        }
    ]
    result = _parse_skyserver_json(payload, query="SELECT TOP 2 ...", dr="18")

    assert result["row_count"] == 2
    assert "objID" in result["columns"]  # preserve original SkyServer casing
    assert "ra" in result["columns"]
    assert result["column_aliases"]["objid"] == "objID"
    assert result["data"]["objID"] == [1237645877629878395, 1237645877629878396]
    assert result["data"]["ra"] == [180.0, 180.1]
    assert result["service"] == "sdss"
    assert result["dr"] == "18"


def test_parse_skyserver_dict_wrapper():
    """Some responses are directly {"Rows": [...]} without the outer list wrapper."""
    payload = {"Rows": [{"z": 0.1, "class": "GALAXY"}]}
    result = _parse_skyserver_json(payload, query="...", dr="18")
    assert result["row_count"] == 1
    assert result["data"]["z"] == [0.1]
    assert result["data"]["class"] == ["GALAXY"]


def test_parse_skyserver_empty_response():
    """Empty result (0 rows) must not raise."""
    result = _parse_skyserver_json([{"Rows": []}], query="...", dr="18")
    assert result["row_count"] == 0
    assert result["columns"] == []
    assert result["data"] == {}


def test_parse_skyserver_null_cells_converted_to_none():
    """SkyServer returns the string 'NULL' (sometimes empty string) for NULL values;
    these must be converted to None so downstream json.dumps or DataFrame handles them correctly."""
    payload = [{"Rows": [{"x": "NULL", "y": "", "z": 0.5}]}]
    result = _parse_skyserver_json(payload, query="...", dr="18")
    assert result["data"]["x"] == [None]
    assert result["data"]["y"] == [None]
    assert result["data"]["z"] == [0.5]


# ---------- execute_sdss_sql boundary + defensive checks ----------

def test_execute_rejects_empty_query():
    with pytest.raises(ValueError, match="empty"):
        asyncio.run(execute_sdss_sql("", dr="18"))


def test_execute_rejects_bad_dr():
    with pytest.raises(ValueError, match="Unsupported SDSS DR"):
        asyncio.run(execute_sdss_sql("SELECT 1", dr="99"))


def test_execute_rejects_dangerous_keywords():
    """DROP / DELETE / INSERT / UPDATE / ALTER / CREATE are not allowed — even though
    SkyServer is a read-only account, we reject these client-side to prevent accidental misuse."""
    for bad in ("DROP TABLE PhotoObjAll", "DELETE FROM SpecObj",
                "INSERT INTO x VALUES (1)", "UPDATE x SET y=1",
                "ALTER TABLE x ADD COLUMN y", "CREATE TABLE x (y int)"):
        with pytest.raises(ValueError, match="Forbidden keyword"):
            asyncio.run(execute_sdss_sql(bad, dr="18"))


# ---------- execute_sdss_sql success paths ----------

def _make_fake_httpx_response(status: int, json_data=None, text: str | None = None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    if status >= 400:
        err = httpx.HTTPStatusError(
            f"{status}", request=MagicMock(), response=resp
        )
        resp.raise_for_status = MagicMock(side_effect=err)
    else:
        resp.raise_for_status = MagicMock()
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
        resp.text = json.dumps(json_data)
    else:
        resp.json = MagicMock(side_effect=ValueError("not json"))
        resp.text = text or ""
    return resp


def test_execute_success_returns_parsed_rows():
    fake_payload = [{"Rows": [{"objID": 123, "ra": 100.0, "r": 18.0}]}]
    fake_resp = _make_fake_httpx_response(200, json_data=fake_payload)

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch("app.connectors.sdss_sql.httpx.AsyncClient", return_value=fake_client):
        result = asyncio.run(execute_sdss_sql("SELECT TOP 1 objID, ra, r FROM PhotoObjAll WHERE mode=1", dr="18"))

    assert result["row_count"] == 1
    assert result["data"]["objID"] == [123]
    assert result["column_aliases"]["objid"] == "objID"
    # confirm it is a GET request with format=json and cmd=<query>
    call_kwargs = fake_client.get.call_args
    assert call_kwargs.kwargs["params"]["format"] == "json"
    assert "SELECT" in call_kwargs.kwargs["params"]["cmd"]


def test_execute_retries_transient_skyserver_failure():
    """On transient SkyServer connection/timeout errors, the long-mode caller retries up to 3 times."""
    fake_payload = [{"Rows": [{"objID": 123, "ra": 100.0}]}]
    fake_resp = _make_fake_httpx_response(200, json_data=fake_payload)

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get = AsyncMock(side_effect=[
        httpx.ConnectTimeout("temporary SkyServer outage"),
        fake_resp,
    ])

    with patch("app.connectors.sdss_sql.httpx.AsyncClient", return_value=fake_client):
        result = asyncio.run(execute_sdss_sql(
            "SELECT TOP 1 objID, ra FROM PhotoObjAll WHERE mode=1",
            dr="18",
            max_attempts=2,
            backoff_s=0,
        ))

    assert result["row_count"] == 1
    assert fake_client.get.await_count == 2


def test_execute_sql_syntax_error_surfaces_as_valueerror():
    """SkyServer returns HTML/text (not JSON) for SQL errors — we convert it to a clear
    ValueError so callers can catch it and instruct the AI to fix the query."""
    fake_resp = _make_fake_httpx_response(
        200, json_data=None,
        text="<html>Syntax error near 'TOP'</html>",
    )
    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch("app.connectors.sdss_sql.httpx.AsyncClient", return_value=fake_client):
        with pytest.raises(ValueError, match="did not return JSON"):
            asyncio.run(execute_sdss_sql("SELECT BORKED FROM NOWHERE", dr="18"))
