"""C2: versioned provenance environment manifest tests."""

from app.services import provenance


def _reset():
    provenance._provenance_records.clear()
    provenance._env_manifest = None


def test_environment_manifest_has_fingerprint():
    _reset()
    manifest = provenance.get_environment_manifest()
    assert "fingerprint" in manifest
    assert len(manifest["fingerprint"]) == 16
    assert "versions" in manifest
    assert manifest["versions"]["python"]


def test_manifest_is_cached():
    _reset()
    m1 = provenance.get_environment_manifest()
    m2 = provenance.get_environment_manifest()
    assert m1 is m2  # exact object identity — cached


def test_record_activity_captures_manifest():
    _reset()
    rec_id = provenance.record_activity(
        entity_type="test",
        entity_id="e1",
        activity="unit_test",
    )
    assert rec_id
    rec = provenance._provenance_records[-1]
    assert "fingerprint" in rec["environment"]
    assert rec["environment"]["fingerprint"] != "unknown"
    assert "system_prompt_hash" in rec["environment"]


def test_record_activity_accepts_data_release():
    _reset()
    provenance.record_activity(
        entity_type="search",
        entity_id="e2",
        activity="gaia_cone_search",
        data_release="Gaia DR3",
    )
    rec = provenance._provenance_records[-1]
    assert rec["data_release"] == "Gaia DR3"


def test_caller_environment_overrides_manifest():
    _reset()
    provenance.record_activity(
        entity_type="pipeline",
        entity_id="e3",
        activity="run",
        environment={"fingerprint": "custom-override"},
    )
    rec = provenance._provenance_records[-1]
    assert rec["environment"]["fingerprint"] == "custom-override"
