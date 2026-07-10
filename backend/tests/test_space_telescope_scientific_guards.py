"""Scientific guards for space-observatory proposal estimates."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize("telescope", ["hst", "jwst", "HST", "JWST"])
def test_generic_ground_etc_rejects_space_telescopes(telescope):
    from app.services.astro_analysis import exposure_time_estimate

    with pytest.raises(ValueError, match="official STScI ETC"):
        exposure_time_estimate(20.0, telescope=telescope)


async def test_space_proposal_never_uses_ground_visibility_or_etc(monkeypatch):
    from app.connectors.simbad import SIMBADConnector
    from app.services.ai_tools import research_workflow

    monkeypatch.setattr(
        "app.services.name_resolver.resolve_name",
        AsyncMock(
            return_value=SimpleNamespace(
                resolved=True,
                ra=10.684,
                dec=41.269,
                aliases_tried=["M31"],
            )
        ),
    )
    monkeypatch.setattr(
        SIMBADConnector,
        "get_object_detail",
        AsyncMock(return_value={"object_type": "Galaxy", "extra": {"V": 4.36}}),
    )
    monkeypatch.setattr(
        research_workflow,
        "_exec_literature",
        AsyncMock(return_value={"results": []}),
    )

    result = await research_workflow._exec_generate_proposal(
        {
            "target_name": "M31",
            "telescope": "jwst",
            "instrument": "NIRCam",
            "science_goal": "imaging",
        }
    )

    assert result["visibility"]["status"] == "not_applicable"
    assert "Ground altitude" in result["visibility"]["reason"]
    assert result["exposure_estimate"]["status"] == "not_available"
    assert "official STScI instrument ETC" in result["exposure_estimate"]["reason"]
    assert any("no ground proxy" in note for note in result["notes"])
