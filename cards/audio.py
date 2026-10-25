"""Generate and cache Arabic pronunciation audio."""
import hashlib
import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings
from gtts import gTTS

logger = logging.getLogger(__name__)


class ArabicAudioError(RuntimeError):
    pass


def audio_cache_dir():
    configured = getattr(settings, "ARABIC_AUDIO_CACHE_DIR", None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR) / "media" / "arabic_audio"


def audio_filename(text):
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
    return f"{digest}.mp3"


def get_or_create_arabic_audio(text):
    """Return a cached MP3 for ``text``, generating it on first use."""
    text = (text or "").strip()
    if not text:
        raise ArabicAudioError("No Arabic text was supplied.")

    cache_dir = audio_cache_dir()
    audio_path = cache_dir / audio_filename(text)
    if audio_path.is_file() and audio_path.stat().st_size > 0:
        return audio_path

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        file_descriptor, temp_name = tempfile.mkstemp(
            dir=cache_dir,
            prefix=".arabic-audio-",
            suffix=".mp3",
        )
        os.close(file_descriptor)
        temp_path = Path(temp_name)
        try:
            gTTS(text=text, lang="ar", slow=False, timeout=15).save(
                str(temp_path)
            )
            if temp_path.stat().st_size == 0:
                raise ArabicAudioError("The generated audio file is empty.")
            temp_path.replace(audio_path)
        finally:
            temp_path.unlink(missing_ok=True)
    except ArabicAudioError:
        raise
    except Exception as error:
        logger.exception("Could not generate Arabic audio")
        raise ArabicAudioError("The audio could not be generated.") from error

    return audio_path
