"""
State machine for recording jobs.

Enforces valid transitions between RecordingStatus values.
All state changes MUST go through StateMachine.transition().
"""

from __future__ import annotations

from yt_live_archiver.models import RecordingStatus

# ---------------------------------------------------------------------------
# Allowed transitions
# ---------------------------------------------------------------------------

# Maps each source state to the set of valid destination states.
_ALLOWED_TRANSITIONS: dict[RecordingStatus, set[RecordingStatus]] = {
    RecordingStatus.DISCOVERED: {RecordingStatus.RECORDING, RecordingStatus.RECORDING_FAILED},
    RecordingStatus.RECORDING: {
        RecordingStatus.FINALIZING,
        RecordingStatus.RECORDING_FAILED,
    },
    RecordingStatus.FINALIZING: {
        RecordingStatus.VERIFYING,
        RecordingStatus.RECORDING_FAILED,
        RecordingStatus.VERIFICATION_FAILED,
    },
    RecordingStatus.VERIFYING: {
        RecordingStatus.VERIFIED,
        RecordingStatus.VERIFICATION_FAILED,
    },
    RecordingStatus.VERIFIED: {
        RecordingStatus.UPLOADING,
        RecordingStatus.UPLOAD_FAILED,
    },
    RecordingStatus.UPLOADING: {
        RecordingStatus.UPLOADED,
        RecordingStatus.UPLOAD_FAILED,
    },
    RecordingStatus.UPLOADED: {
        RecordingStatus.NOTIFYING,
        RecordingStatus.NOTIFICATION_FAILED,
        RecordingStatus.COMPLETED,  # When webhook is not required
    },
    RecordingStatus.NOTIFYING: {
        RecordingStatus.COMPLETED,
        RecordingStatus.NOTIFICATION_FAILED,
    },
    RecordingStatus.COMPLETED: set(),
    # Failure states can re-enter the pipeline from specific points
    RecordingStatus.RECORDING_FAILED: {RecordingStatus.RECORDING},
    RecordingStatus.VERIFICATION_FAILED: {RecordingStatus.VERIFYING},
    RecordingStatus.UPLOAD_FAILED: {RecordingStatus.UPLOADING},
    RecordingStatus.NOTIFICATION_FAILED: {RecordingStatus.NOTIFYING},
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


class StateMachine:
    """Validates state transitions for a Recording.

    Usage:
        sm = StateMachine()
        sm.validate(current_status, RecordingStatus.RECORDING)  # raises on invalid
        sm.transition(recording, RecordingStatus.RECORDING)     # modifies in-place
    """

    def validate(self, from_status: RecordingStatus, to_status: RecordingStatus) -> None:
        """Raise InvalidTransitionError if the transition is not allowed."""
        allowed = _ALLOWED_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise InvalidTransitionError(
                f"Invalid transition: {from_status.value} → {to_status.value}. "
                f"Allowed from {from_status.value}: "
                f"{', '.join(s.value for s in allowed) or 'none'}"
            )

    def transition(self, recording: "Recording", to_status: RecordingStatus) -> None:  # noqa: F821
        """Validate and apply a state transition to *recording* in-place.

        Raises InvalidTransitionError if the transition is not allowed.
        """
        from yt_live_archiver.models import Recording  # local import to avoid circularity

        if not isinstance(recording, Recording):
            raise TypeError(f"Expected Recording, got {type(recording)}")

        self.validate(recording.status, to_status)
        recording.status = to_status

    def can_transition(self, from_status: RecordingStatus, to_status: RecordingStatus) -> bool:
        """Return True if the transition is allowed without raising."""
        return to_status in _ALLOWED_TRANSITIONS.get(from_status, set())

    def allowed_next(self, from_status: RecordingStatus) -> set[RecordingStatus]:
        """Return all allowed destination states from *from_status*."""
        return set(_ALLOWED_TRANSITIONS.get(from_status, set()))


# Module-level singleton for convenience
state_machine = StateMachine()
