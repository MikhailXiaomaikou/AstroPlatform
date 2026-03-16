"""Tests for SQLAlchemy models and custom TypeDecorators."""

import json
import uuid

import pytest
from sqlalchemy import create_engine, Column, Integer
from sqlalchemy.orm import Session, DeclarativeBase

from app.models.schemas import (
    DataFile,
    DataNote,
    DataTag,
    JSONType,
    PipelineComment,
    PipelineRun,
    PipelineTemplateDB,
    PipelineVersion,
    RunResult,
    ScheduledRun,
    SharedDataset,
    SharedPipeline,
    TeamMember,
    User,
    UUIDType,
)


class TestModelInstantiation:
    """Verify every ORM model can be instantiated with defaults."""

    def test_user(self):
        u = User(email="a@b.com", password_hash="hash", subscription_tier="solo")
        assert u.email == "a@b.com"
        assert u.subscription_tier == "solo"

    def test_data_file(self):
        uid = uuid.uuid4()
        df = DataFile(user_id=uid, source="sdss", object_id="12345")
        assert df.source == "sdss"

    def test_pipeline_run(self):
        uid = uuid.uuid4()
        pr = PipelineRun(user_id=uid, dag={"nodes": [], "edges": []}, status="pending")
        assert pr.status == "pending"

    def test_run_result(self):
        rr = RunResult(run_id=uuid.uuid4(), node_id="n1")
        assert rr.node_id == "n1"

    def test_pipeline_template(self):
        tpl = PipelineTemplateDB(name="Test", dag={"nodes": [], "edges": []}, is_builtin=False)
        assert tpl.is_builtin is False

    def test_data_tag(self):
        dt = DataTag(user_id=uuid.uuid4(), data_file_id=uuid.uuid4(), tag="galaxy")
        assert dt.tag == "galaxy"

    def test_data_note(self):
        dn = DataNote(user_id=uuid.uuid4(), data_file_id=uuid.uuid4(), content="interesting")
        assert dn.content == "interesting"

    def test_pipeline_version(self):
        pv = PipelineVersion(template_id=uuid.uuid4(), dag={"nodes": []}, version=1)
        assert pv.version == 1

    def test_team_member(self):
        tm = TeamMember(owner_id=uuid.uuid4(), member_id=uuid.uuid4(), role="member")
        assert tm.role == "member"

    def test_shared_pipeline(self):
        sp = SharedPipeline(
            template_id=uuid.uuid4(),
            shared_by=uuid.uuid4(),
            shared_with=uuid.uuid4(),
            permission="view",
        )
        assert sp.permission == "view"

    def test_pipeline_comment(self):
        pc = PipelineComment(
            template_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            content="looks good",
        )
        assert pc.content == "looks good"

    def test_shared_dataset(self):
        sd = SharedDataset(
            data_file_id=uuid.uuid4(),
            shared_by=uuid.uuid4(),
            shared_with=uuid.uuid4(),
        )
        assert sd.data_file_id is not None

    def test_scheduled_run(self):
        sr = ScheduledRun(
            user_id=uuid.uuid4(),
            name="nightly",
            dag={"nodes": []},
            input_data_id="abc",
            cron_expr="0 0 * * *",
            enabled=True,
        )
        assert sr.enabled is True


class TestUUIDType:
    """Test the UUIDType TypeDecorator serialization."""

    def test_bind_param_converts_to_string(self):
        td = UUIDType()
        uid = uuid.uuid4()
        result = td.process_bind_param(uid, None)
        assert isinstance(result, str)
        assert result == str(uid)

    def test_bind_param_none_stays_none(self):
        td = UUIDType()
        assert td.process_bind_param(None, None) is None

    def test_result_value_converts_to_uuid(self):
        td = UUIDType()
        uid = uuid.uuid4()
        result = td.process_result_value(str(uid), None)
        assert isinstance(result, uuid.UUID)
        assert result == uid

    def test_result_value_none_stays_none(self):
        td = UUIDType()
        assert td.process_result_value(None, None) is None

    def test_result_value_already_uuid(self):
        td = UUIDType()
        uid = uuid.uuid4()
        result = td.process_result_value(uid, None)
        assert result == uid


class TestJSONType:
    """Test the JSONType TypeDecorator serialization."""

    def test_bind_param_serializes_dict(self):
        td = JSONType()
        data = {"nodes": [1, 2], "key": "val"}
        result = td.process_bind_param(data, None)
        assert isinstance(result, str)
        assert json.loads(result) == data

    def test_bind_param_none_stays_none(self):
        td = JSONType()
        assert td.process_bind_param(None, None) is None

    def test_result_value_deserializes(self):
        td = JSONType()
        data = {"a": [1, 2, 3]}
        result = td.process_result_value(json.dumps(data), None)
        assert result == data

    def test_result_value_none_stays_none(self):
        td = JSONType()
        assert td.process_result_value(None, None) is None

    def test_round_trip_complex_data(self):
        td = JSONType()
        data = {
            "nodes": [{"id": "n1", "type": "Denoise"}],
            "edges": [],
            "nested": {"deep": True, "count": 42},
        }
        serialized = td.process_bind_param(data, None)
        deserialized = td.process_result_value(serialized, None)
        assert deserialized == data
