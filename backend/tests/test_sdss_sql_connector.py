"""J3 — SDSS SkyServer 直连 connector 单元测试.

不打真实网络, 全部用 httpx mock.
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
    """SkyServer format=json 通常返回 [{"Rows": [...]}]."""
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
    assert "objID" in result["columns"]  # 保留 SkyServer 原始大小写
    assert "ra" in result["columns"]
    assert result["column_aliases"]["objid"] == "objID"
    assert result["data"]["objID"] == [1237645877629878395, 1237645877629878396]
    assert result["data"]["ra"] == [180.0, 180.1]
    assert result["service"] == "sdss"
    assert result["dr"] == "18"


def test_parse_skyserver_dict_wrapper():
    """少数响应直接是 {"Rows": [...]} 不裹在 list 里."""
    payload = {"Rows": [{"z": 0.1, "class": "GALAXY"}]}
    result = _parse_skyserver_json(payload, query="...", dr="18")
    assert result["row_count"] == 1
    assert result["data"]["z"] == [0.1]
    assert result["data"]["class"] == ["GALAXY"]


def test_parse_skyserver_empty_response():
    """空结果 (0 行) 不应抛错."""
    result = _parse_skyserver_json([{"Rows": []}], query="...", dr="18")
    assert result["row_count"] == 0
    assert result["columns"] == []
    assert result["data"] == {}


def test_parse_skyserver_null_cells_converted_to_none():
    """SkyServer 对 NULL 返回字符串 'NULL' (有时是空串); 必须转 None
    以便下游 json.dumps 或 DataFrame 正确处理."""
    payload = [{"Rows": [{"x": "NULL", "y": "", "z": 0.5}]}]
    result = _parse_skyserver_json(payload, query="...", dr="18")
    assert result["data"]["x"] == [None]
    assert result["data"]["y"] == [None]
    assert result["data"]["z"] == [0.5]


# ---------- execute_sdss_sql 边界 + 防御 ----------

def test_execute_rejects_empty_query():
    with pytest.raises(ValueError, match="empty"):
        asyncio.run(execute_sdss_sql("", dr="18"))


def test_execute_rejects_bad_dr():
    with pytest.raises(ValueError, match="Unsupported SDSS DR"):
        asyncio.run(execute_sdss_sql("SELECT 1", dr="99"))


def test_execute_rejects_dangerous_keywords():
    """不允许 DROP / DELETE / INSERT / UPDATE / ALTER / CREATE — 即便
    SkyServer 本身是只读账号, 我们也在客户端拒绝以免误用."""
    for bad in ("DROP TABLE PhotoObjAll", "DELETE FROM SpecObj",
                "INSERT INTO x VALUES (1)", "UPDATE x SET y=1",
                "ALTER TABLE x ADD COLUMN y", "CREATE TABLE x (y int)"):
        with pytest.raises(ValueError, match="Forbidden keyword"):
            asyncio.run(execute_sdss_sql(bad, dr="18"))


# ---------- execute_sdss_sql 成功路径 ----------

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
    # 确认是 GET + format=json + cmd=<query>
    call_kwargs = fake_client.get.call_args
    assert call_kwargs.kwargs["params"]["format"] == "json"
    assert "SELECT" in call_kwargs.kwargs["params"]["cmd"]


def test_execute_retries_transient_skyserver_failure():
    """SkyServer 偶发连接/超时错误时, long-mode 调用方会给 3 次机会。"""
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
    """SkyServer 对 SQL 错误返回 HTML/text 不是 JSON — 我们转成清晰的
    ValueError 让调用方捕获后告诉 AI 改 query."""
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
