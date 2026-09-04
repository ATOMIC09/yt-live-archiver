"""Unit tests for the state machine."""

from __future__ import annotations

import pytest

from yt_live_archiver.models import Recording, RecordingStatus
from yt_live_archiver.state_machine import InvalidTransitionError, StateMachine, state_machine


class TestStateMachine:
    def setup_method(self):
        self.sm = StateMachine()

    def _recording(self, status: RecordingStatus) -> Recording:
        r = Recording()
        r.status = status
        return r

    # Valid transitions
    def test_discovered_to_recording(self):
        r = self._recording(RecordingStatus.DISCOVERED)
        self.sm.transition(r, RecordingStatus.RECORDING)
        assert r.status == RecordingStatus.RECORDING

    def test_recording_to_finalizing(self):
        r = self._recording(RecordingStatus.RECORDING)
        self.sm.transition(r, RecordingStatus.FINALIZING)
        assert r.status == RecordingStatus.FINALIZING

    def test_full_normal_pipeline(self):
        flow = [
            RecordingStatus.DISCOVERED,
            RecordingStatus.RECORDING,
            RecordingStatus.FINALIZING,
            RecordingStatus.VERIFYING,
            RecordingStatus.VERIFIED,
            RecordingStatus.UPLOADING,
            RecordingStatus.UPLOADED,
            RecordingStatus.NOTIFYING,
            RecordingStatus.COMPLETED,
        ]
        r = self._recording(RecordingStatus.DISCOVERED)
        for i in range(1, len(flow)):
            self.sm.transition(r, flow[i])
            assert r.status == flow[i]

    def test_recording_to_failed(self):
        r = self._recording(RecordingStatus.RECORDING)
        self.sm.transition(r, RecordingStatus.RECORDING_FAILED)
        assert r.status == RecordingStatus.RECORDING_FAILED

    def test_upload_failed_can_retry(self):
        r = self._recording(RecordingStatus.UPLOAD_FAILED)
        self.sm.transition(r, RecordingStatus.UPLOADING)
        assert r.status == RecordingStatus.UPLOADING

    def test_notification_failed_can_retry(self):
        r = self._recording(RecordingStatus.NOTIFICATION_FAILED)
        self.sm.transition(r, RecordingStatus.NOTIFYING)
        assert r.status == RecordingStatus.NOTIFYING

    # Invalid transitions
    def test_invalid_transition_raises(self):
        r = self._recording(RecordingStatus.DISCOVERED)
        with pytest.raises(InvalidTransitionError):
            self.sm.transition(r, RecordingStatus.UPLOADING)

    def test_completed_has_no_next_states(self):
        r = self._recording(RecordingStatus.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            self.sm.transition(r, RecordingStatus.RECORDING)

    def test_can_transition_returns_false_for_invalid(self):
        assert not self.sm.can_transition(
            RecordingStatus.COMPLETED, RecordingStatus.RECORDING
        )

    def test_can_transition_returns_true_for_valid(self):
        assert self.sm.can_transition(
            RecordingStatus.VERIFIED, RecordingStatus.UPLOADING
        )

    def test_validate_raises_for_wrong_type(self):
        with pytest.raises(TypeError):
            self.sm.transition("not_a_recording", RecordingStatus.RECORDING)  # type: ignore

    def test_allowed_next_discovered(self):
        allowed = self.sm.allowed_next(RecordingStatus.DISCOVERED)
        assert RecordingStatus.RECORDING in allowed
        assert RecordingStatus.RECORDING_FAILED in allowed

    def test_status_flags(self):
        assert RecordingStatus.COMPLETED.is_terminal
        assert not RecordingStatus.RECORDING.is_terminal
        assert RecordingStatus.UPLOAD_FAILED.is_failed
        assert not RecordingStatus.VERIFIED.is_failed
        assert RecordingStatus.UPLOADING.is_recoverable
        assert not RecordingStatus.COMPLETED.is_recoverable
