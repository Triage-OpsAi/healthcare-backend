"""
Sarvam AI integration.

Docs: https://docs.sarvam.ai  (base URL: https://api.sarvam.ai)
Auth: header 'api-subscription-key: <your key>' on every request.
Get a key: https://dashboard.sarvam.ai -> API Keys -> Create New Key
           (see README for the full walkthrough).

We call the /speech-to-text endpoint twice per clip with model 'saaras:v3':
  - mode='transcribe' -> original-language text (kept for the legal record
    and for doctor review, since translation can lose nuance)
  - mode='translate'  -> English text (what gets structured into the note)

For clips over ~30s, the service automatically falls back to Sarvam's Batch
API (async, up to 2 hours of audio).
"""
import asyncio
import json
import tempfile
from pathlib import Path

import httpx
from sarvamai import AsyncSarvamAI

from app.core.config import settings


class SarvamAPIError(Exception):
    pass


class SarvamAudioTooLongError(SarvamAPIError):
    pass


SUPPORTED_LANGUAGES = {
    "unknown": "Auto-detect",
    "hi-IN": "Hindi",
    "kn-IN": "Kannada",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "pa-IN": "Punjabi",
    "od-IN": "Odia",
    "en-IN": "English (India)",
}

SARVAM_CONTENT_TYPE_ALIASES = {
    # Browsers commonly label an MP4 containing an audio track as video/mp4,
    # while Sarvam's multipart allowlist expects audio/mp4.
    "video/mp4": "audio/mp4",
    "application/mp4": "audio/mp4",
}

# The batch SDK derives the upload Content-Type exclusively from the local
# temporary file's extension. In particular, ".mp4" becomes "video/mp4",
# which Sarvam rejects even when the same MP4 container is valid audio. Use
# an extension that makes the SDK send one of Sarvam's accepted audio types.
BATCH_EXTENSION_BY_CONTENT_TYPE = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mpeg3": ".mp3",
    "audio/x-mpeg-3": ".mp3",
    "audio/x-mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/aac": ".aac",
    "audio/x-aac": ".aac",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/amr": ".amr",
    "audio/x-ms-wma": ".wma",
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "application/octet-stream": ".bin",
}

ACCEPTED_BATCH_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".aac",
    ".aiff",
    ".ogg",
    ".opus",
    ".flac",
    ".m4a",
    ".amr",
    ".wma",
    ".webm",
}


def normalize_audio_content_type(content_type: str) -> str:
    # MediaRecorder commonly returns values such as
    # ``audio/webm;codecs=opus``. Sarvam validates multipart MIME types
    # against an exact allowlist, so transport parameters must not be sent.
    normalized = content_type.lower().strip().split(";", 1)[0].strip()
    if not normalized:
        normalized = "application/octet-stream"
    return SARVAM_CONTENT_TYPE_ALIASES.get(normalized, normalized)


def batch_audio_filename(filename: str, content_type: str) -> str:
    """Return a safe filename whose extension gives the SDK an accepted MIME."""
    source = Path(filename).name
    stem = Path(source).stem or "audio"
    normalized_type = normalize_audio_content_type(content_type)

    extension = BATCH_EXTENSION_BY_CONTENT_TYPE.get(normalized_type)
    original_extension = Path(source).suffix.lower()
    if extension is None and original_extension in ACCEPTED_BATCH_EXTENSIONS:
        extension = original_extension
    if extension is None:
        extension = ".bin"

    # MP4 audio must be called .m4a: Python/SDK maps .mp4 to video/mp4.
    if original_extension == ".mp4" or normalized_type == "audio/mp4":
        extension = ".m4a"
    return f"{stem}{extension}"


async def _call_speech_to_text(
    *,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    language_code: str,
    mode: str,
) -> str:
    url = f"{settings.SARVAM_BASE_URL}/speech-to-text"
    headers = {"api-subscription-key": settings.SARVAM_API_KEY}
    files = {"file": (filename, audio_bytes, normalize_audio_content_type(content_type))}
    data = {
        "model": settings.SARVAM_STT_MODEL,
        "language_code": language_code,
        "mode": mode,  # 'transcribe' | 'translate'
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, headers=headers, files=files, data=data)

    if response.status_code == 401 or response.status_code == 403:
        raise SarvamAPIError(
            "Sarvam API rejected the request -- check SARVAM_API_KEY in your .env"
        )
    if response.status_code == 429:
        raise SarvamAPIError("Sarvam API rate limit hit -- back off and retry")
    if response.status_code >= 400:
        if response.status_code == 400 and "Audio duration exceeds" in response.text:
            raise SarvamAudioTooLongError("Audio exceeds the 30-second REST limit")
        raise SarvamAPIError(f"Sarvam API error {response.status_code}: {response.text}")

    payload = response.json()
    return payload.get("transcript", "")


def _read_batch_transcript(output_dir: Path) -> str:
    for output_file in output_dir.rglob("*.json"):
        payload = json.loads(output_file.read_text(encoding="utf-8"))
        transcript = payload.get("transcript")
        if isinstance(transcript, str):
            return transcript
    raise SarvamAPIError("Sarvam batch output contained no transcript")


async def _run_batch_mode(
    client: AsyncSarvamAI,
    *,
    audio_path: Path,
    output_dir: Path,
    language_code: str,
    mode: str,
) -> str:
    try:
        job = await client.speech_to_text_job.create_job(
            model=settings.SARVAM_STT_MODEL,
            mode=mode,
            language_code=language_code,
        )
        uploaded = await job.upload_files(file_paths=[str(audio_path)], timeout=120.0)
        if not uploaded:
            raise SarvamAPIError(f"Sarvam batch {mode} upload failed")

        await job.start()
        await job.wait_until_complete(poll_interval=5, timeout=600)
        results = await job.get_file_results()
        if results.get("failed"):
            message = results["failed"][0].get("error_message") or "unknown batch error"
            raise SarvamAPIError(f"Sarvam batch {mode} failed: {message}")

        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded = await job.download_outputs(output_dir=str(output_dir))
        if not downloaded:
            raise SarvamAPIError(f"Sarvam batch {mode} result download failed")
        return _read_batch_transcript(output_dir)
    except SarvamAPIError:
        raise
    except Exception as exc:
        raise SarvamAPIError(f"Sarvam batch {mode} failed: {exc}") from exc


async def _batch_transcribe_and_translate(
    *,
    audio_bytes: bytes,
    filename: str,
    content_type: str,
    language_code: str,
) -> dict:
    safe_filename = batch_audio_filename(filename, content_type)
    with tempfile.TemporaryDirectory(prefix="sarvam-batch-") as temp_dir:
        temp_path = Path(temp_dir)
        audio_path = temp_path / safe_filename
        audio_path.write_bytes(audio_bytes)

        async with httpx.AsyncClient(timeout=120.0) as http_client:
            client = AsyncSarvamAI(
                api_subscription_key=settings.SARVAM_API_KEY,
                httpx_client=http_client,
            )
            original_transcript, english_translation = await asyncio.gather(
                _run_batch_mode(
                    client,
                    audio_path=audio_path,
                    output_dir=temp_path / "transcribe",
                    language_code=language_code,
                    mode="transcribe",
                ),
                _run_batch_mode(
                    client,
                    audio_path=audio_path,
                    output_dir=temp_path / "translate",
                    language_code=language_code,
                    mode="translate",
                ),
            )

    return {
        "raw_transcript": original_transcript,
        "translated_text": english_translation,
    }


async def transcribe_and_translate(
    *,
    audio_bytes: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
    language_code: str = "unknown",
) -> dict:
    """Returns both the original-language transcript and the English translation.

    Two calls is deliberate: we never derive the "legal" original-language
    transcript from the translation (or vice versa) -- each comes straight
    from Sarvam so neither is a second-hand paraphrase of the other.
    """
    try:
        original_transcript = await _call_speech_to_text(
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            language_code=language_code,
            mode="transcribe",
        )
        english_translation = await _call_speech_to_text(
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            language_code=language_code,
            mode="translate",
        )
    except SarvamAudioTooLongError:
        return await _batch_transcribe_and_translate(
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            language_code=language_code,
        )
    return {
        "raw_transcript": original_transcript,
        "translated_text": english_translation,
    }
