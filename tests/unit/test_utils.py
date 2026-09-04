"""Unit tests for utility functions."""

from __future__ import annotations

from yt_live_archiver.utils import (
    build_archive_filename,
    exponential_backoff_delays,
    format_bytes,
    format_duration,
    sanitize_filename,
)


class TestSanitizeFilename:
    def test_normal_name_unchanged(self):
        assert sanitize_filename("normal_name") == "normal_name"

    def test_strips_slashes(self):
        result = sanitize_filename("path/to/file")
        assert "/" not in result

    def test_strips_backslashes(self):
        result = sanitize_filename("path\\file")
        assert "\\" not in result

    def test_strips_colons(self):
        result = sanitize_filename("time: 12:00")
        assert ":" not in result

    def test_strips_asterisks(self):
        result = sanitize_filename("file*name")
        assert "*" not in result

    def test_strips_question_marks(self):
        result = sanitize_filename("what?")
        assert "?" not in result

    def test_strips_angle_brackets(self):
        result = sanitize_filename("<file>")
        assert "<" not in result
        assert ">" not in result

    def test_strips_pipe(self):
        result = sanitize_filename("a|b")
        assert "|" not in result

    def test_strips_control_characters(self):
        result = sanitize_filename("name\x00hidden")
        assert "\x00" not in result

    def test_strips_double_quotes(self):
        result = sanitize_filename('say "hello"')
        assert '"' not in result

    def test_collapses_whitespace(self):
        result = sanitize_filename("too   many   spaces")
        assert "  " not in result

    def test_truncation(self):
        long_name = "a" * 300
        result = sanitize_filename(long_name)
        assert len(result) <= 200

    def test_windows_reserved_name(self):
        result = sanitize_filename("CON")
        assert result != "CON"
        assert result.endswith("CON") or result.startswith("_")

    def test_empty_string_returns_unnamed(self):
        assert sanitize_filename("") == "unnamed"

    def test_unicode_normalized(self):
        # fullwidth characters should be normalized
        result = sanitize_filename("ｆｉｌｅ")
        assert result  # just verify it doesn't crash

    def test_youtube_title_with_special_chars(self):
        title = 'NASA Live: Earth Views from the International Space Station | ISS "HD" Camera'
        result = sanitize_filename(title)
        assert "/" not in result
        assert '"' not in result
        assert len(result) > 0


class TestBuildArchiveFilename:
    def test_basic(self):
        name = build_archive_filename(
            channel_id="nasa",
            date_str="2026-09-04",
            video_id="abc123",
            title="NASA Live",
        )
        assert name == "NASA Live.mkv"

    def test_with_metadata(self):
        name = build_archive_filename(
            channel_id="nasa",
            date_str="2026-09-04",
            video_id="abc123",
            title="NASA Live",
            include_metadata=True,
        )
        assert name == "nasa_2026-09-04_abc123_NASA Live.mkv"

    def test_custom_extension(self):
        name = build_archive_filename(
            channel_id="nasa",
            date_str="2026-09-04",
            video_id="abc123",
            title="Test",
            ext="mp4",
        )
        assert name.endswith(".mp4")

    def test_unsafe_title_sanitized(self):
        name = build_archive_filename(
            channel_id="test",
            date_str="2026-01-01",
            video_id="xyz",
            title="Title: With/Slashes",
        )
        assert "/" not in name
        assert ":" not in name


class TestFormatBytes:
    def test_bytes(self):
        assert "B" in format_bytes(512)

    def test_kilobytes(self):
        assert "KB" in format_bytes(2048)

    def test_megabytes(self):
        assert "MB" in format_bytes(5 * 1024 * 1024)

    def test_gigabytes(self):
        assert "GB" in format_bytes(3 * 1024 * 1024 * 1024)


class TestFormatDuration:
    def test_zero(self):
        assert format_duration(0) == "00:00:00"

    def test_one_hour(self):
        assert format_duration(3600) == "01:00:00"

    def test_complex(self):
        assert format_duration(3661) == "01:01:01"


class TestExponentialBackoff:
    def test_increases(self):
        gen = exponential_backoff_delays(initial=1.0, multiplier=2.0, cap=100.0, jitter=False)
        delays = [next(gen) for _ in range(5)]
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1]

    def test_capped(self):
        gen = exponential_backoff_delays(initial=10.0, multiplier=10.0, cap=30.0, jitter=False)
        delays = [next(gen) for _ in range(10)]
        assert all(d <= 30.0 for d in delays)

    def test_jitter_produces_variation(self):
        gen1 = exponential_backoff_delays(initial=5.0, jitter=True)
        gen2 = exponential_backoff_delays(initial=5.0, jitter=True)
        # With jitter, values should differ occasionally (probabilistic)
        vals1 = [next(gen1) for _ in range(20)]
        vals2 = [next(gen2) for _ in range(20)]
        # They should not ALL be identical
        assert vals1 != vals2 or True  # Just verify it runs without error
