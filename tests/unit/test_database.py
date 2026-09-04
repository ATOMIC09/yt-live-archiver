"""Unit tests for the SQLite database layer."""

from __future__ import annotations

import pytest

from yt_live_archiver.database import Database
from yt_live_archiver.migrations import run_migrations
from yt_live_archiver.models import Recording, RecordingStatus


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database for testing."""
    db_path = str(tmp_path / "test.db")
    db = Database(db_path)
    run_migrations(db_path)
    return db


def make_recording(video_id: str = "test123", channel_id: str = "testchannel") -> Recording:
    r = Recording()
    r.youtube_video_id = video_id
    r.channel_id = channel_id
    r.channel_name = "Test Channel"
    r.channel_url = "https://www.youtube.com/@TestChannel/live"
    r.youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    r.title = "Test Stream"
    r.status = RecordingStatus.DISCOVERED
    return r


class TestDatabase:
    def test_create_and_get_by_video_id(self, tmp_db):
        r = make_recording("video001")
        tmp_db.create_recording(r)
        assert r.id is not None

        fetched = tmp_db.get_by_video_id("video001")
        assert fetched is not None
        assert fetched.youtube_video_id == "video001"
        assert fetched.channel_id == "testchannel"

    def test_get_nonexistent_returns_none(self, tmp_db):
        result = tmp_db.get_by_video_id("nonexistent")
        assert result is None

    def test_video_id_exists(self, tmp_db):
        r = make_recording("exists123")
        tmp_db.create_recording(r)
        assert tmp_db.video_id_exists("exists123") is True
        assert tmp_db.video_id_exists("missing") is False

    def test_duplicate_video_id_raises(self, tmp_db):
        r1 = make_recording("dup123")
        r2 = make_recording("dup123")
        tmp_db.create_recording(r1)
        with pytest.raises(Exception):
            tmp_db.create_recording(r2)

    def test_update_recording(self, tmp_db):
        r = make_recording("upd001")
        tmp_db.create_recording(r)

        r.status = RecordingStatus.RECORDING
        r.title = "Updated Title"
        tmp_db.update_recording(r)

        fetched = tmp_db.get_by_video_id("upd001")
        assert fetched.status == RecordingStatus.RECORDING
        assert fetched.title == "Updated Title"

    def test_get_all_with_status(self, tmp_db):
        r1 = make_recording("st001")
        r2 = make_recording("st002")
        r3 = make_recording("st003")

        tmp_db.create_recording(r1)
        tmp_db.create_recording(r2)
        tmp_db.create_recording(r3)

        r2.status = RecordingStatus.RECORDING
        tmp_db.update_recording(r2)
        r3.status = RecordingStatus.VERIFIED
        tmp_db.update_recording(r3)

        discovered = tmp_db.get_all_with_status(RecordingStatus.DISCOVERED)
        assert len(discovered) == 1
        assert discovered[0].youtube_video_id == "st001"

        recording_and_verified = tmp_db.get_all_with_status(
            RecordingStatus.RECORDING, RecordingStatus.VERIFIED
        )
        assert len(recording_and_verified) == 2

    def test_set_error(self, tmp_db):
        r = make_recording("err001")
        tmp_db.create_recording(r)
        tmp_db.set_error(r, "Something went wrong")

        fetched = tmp_db.get_by_video_id("err001")
        assert fetched.last_error == "Something went wrong"
        assert fetched.last_error_at is not None

    def test_persist_verification_flags(self, tmp_db):
        r = make_recording("vf001")
        r.media_verified = True
        r.drive_verified = True
        r.webhook_sent = True
        r.drive_file_id = "drive_file_123"
        tmp_db.create_recording(r)
        r.status = RecordingStatus.COMPLETED
        tmp_db.update_recording(r)

        fetched = tmp_db.get_by_video_id("vf001")
        assert fetched.media_verified is True
        assert fetched.drive_verified is True
        assert fetched.webhook_sent is True
        assert fetched.drive_file_id == "drive_file_123"

    def test_get_all(self, tmp_db):
        for i in range(5):
            tmp_db.create_recording(make_recording(f"all00{i}"))
        all_recs = tmp_db.get_all()
        assert len(all_recs) == 5

    def test_persist_media_metadata(self, tmp_db):
        r = make_recording("meta001")
        r.duration_seconds = 3600.5
        r.container = "matroska"
        r.video_codec = "vp9"
        r.audio_codec = "opus"
        r.width = 1920
        r.height = 1080
        r.fps = 30.0
        tmp_db.create_recording(r)
        tmp_db.update_recording(r)

        fetched = tmp_db.get_by_video_id("meta001")
        assert fetched.duration_seconds == pytest.approx(3600.5)
        assert fetched.container == "matroska"
        assert fetched.video_codec == "vp9"
        assert fetched.width == 1920
        assert fetched.fps == pytest.approx(30.0)
