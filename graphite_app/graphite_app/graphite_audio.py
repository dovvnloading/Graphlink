import mimetypes
import wave
from pathlib import Path

try:
    from mutagen import File as MutagenFile
    MUTAGEN_AVAILABLE = True
except ImportError:
    MutagenFile = None
    MUTAGEN_AVAILABLE = False


SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}

MAX_AUDIO_DURATION_SECONDS = 4 * 60 * 60

_AUDIO_MIME_OVERRIDES = {
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}

class AudioValidationError(ValueError):
    pass


def is_supported_audio_file(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS


def guess_audio_mime_type(file_path: str) -> str:
    extension = Path(file_path).suffix.lower()
    if extension in _AUDIO_MIME_OVERRIDES:
        return _AUDIO_MIME_OVERRIDES[extension]

    guessed, _ = mimetypes.guess_type(file_path)
    return guessed or "application/octet-stream"


def format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "Unknown"

    total_seconds = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:d}:{secs:02d}"


def inspect_audio_file(file_path: str) -> dict:
    path = Path(file_path)
    if not path.is_file():
        raise AudioValidationError(f"File not found: {file_path}")

    if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
        raise AudioValidationError(
            f"Unsupported audio format '{path.suffix.lower() or path.name}'."
        )

    duration_seconds = _probe_audio_duration_seconds(path)
    if duration_seconds is None:
        dependency_hint = ""
        if not MUTAGEN_AVAILABLE:
            dependency_hint = " Install dependencies with: pip install -r requirements.txt"
        raise AudioValidationError(
            f"Could not read the duration for '{path.name}'.{dependency_hint}"
        )

    if duration_seconds <= 0:
        raise AudioValidationError(f"'{path.name}' does not contain a readable audio stream.")

    if duration_seconds > MAX_AUDIO_DURATION_SECONDS:
        raise AudioValidationError(
            f"'{path.name}' is {format_duration(duration_seconds)} long. "
            f"The maximum supported length is {format_duration(MAX_AUDIO_DURATION_SECONDS)}."
        )

    return {
        "path": str(path.resolve()),
        "mime_type": guess_audio_mime_type(str(path)),
        "duration_seconds": float(duration_seconds),
        "byte_size": path.stat().st_size,
    }


def _probe_audio_duration_seconds(path: Path) -> float | None:
    if MUTAGEN_AVAILABLE:
        try:
            parsed = MutagenFile(path)
            info = getattr(parsed, "info", None)
            length = float(getattr(info, "length", 0) or 0)
            if length > 0:
                return length
        except Exception:
            pass

    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wav_file:
                frame_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
                if frame_rate > 0 and frame_count > 0:
                    return frame_count / float(frame_rate)
        except Exception:
            pass

    return None
