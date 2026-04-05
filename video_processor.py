import os
import socket
import subprocess
import time
import uuid
import json
import whisper
from deep_translator import GoogleTranslator


# ── Translation retry settings ────────────────────────────────────────────────
MEMORY_RETRIES = 3       # maximum per-segment / per-batch attempts per backend
_MAX_RETRIES = MEMORY_RETRIES
_BASE_DELAY  = 1.0     # seconds before first retry
_MAX_DELAY   = 15.0    # cap on exponential back-off


class NoInternetError(RuntimeError):
    """Raised when DNS resolution fails — genuine loss of internet connectivity."""
    pass


def _is_no_internet(exc):
    """Walk the exception chain; return True if a DNS failure (socket.gaierror) is found."""
    seen = set()
    cause = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, socket.gaierror):
            return True
        cause = getattr(cause, '__cause__', None) or getattr(cause, '__context__', None)
    return False


def _translate_with_retry(translator, text):
    """
    Translate *text* using *translator*, retrying on transient network errors.
    Falls back to MyMemoryTranslator if all Google retries fail.
    Raises NoInternetError if DNS resolution fails (no internet).
    Returns original text if all backends fail.
    """
    delay = _BASE_DELAY
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return translator.translate(text)
        except Exception as exc:
            if _is_no_internet(exc):
                raise NoInternetError("No internet connection detected.") from exc
            print(f"[translate] Attempt {attempt}/{_MAX_RETRIES} failed "
                  f"({type(exc).__name__}: {exc}). Retrying in {delay:.1f}s…")
            time.sleep(delay)
            delay = min(delay * 1.5, _MAX_DELAY)

    # Google failed — try MyMemory fallback
    try:
        from deep_translator import MyMemoryTranslator
        src = getattr(translator, 'source', 'auto')
        tgt = getattr(translator, 'target', 'en')
        result = MyMemoryTranslator(source=src, target=tgt).translate(text)
        if result:
            print("[translate] MyMemory fallback succeeded.")
            return result
    except Exception as fb_exc:
        print(f"[translate] MyMemory fallback failed: {fb_exc}")

    # All backends failed — return original text
    print("[translate] All backends failed. Using original text.")
    return text


def _translate_batch_with_retry(translator, texts):
    """
    Translate a batch of texts, retrying on transient network errors.
    Falls back to MyMemoryTranslator (per-segment) if all Google retries fail.
    Raises NoInternetError if DNS resolution fails (no internet).
    Returns original texts if all backends fail.
    """
    delay = _BASE_DELAY
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return translator.translate_batch(texts)
        except Exception as exc:
            if _is_no_internet(exc):
                raise NoInternetError("No internet connection detected.") from exc
            print(f"[translate_batch] Attempt {attempt}/{_MAX_RETRIES} failed "
                  f"({type(exc).__name__}: {exc}). Retrying in {delay:.1f}s…")
            time.sleep(delay)
            delay = min(delay * 1.5, _MAX_DELAY)

    # Google batch failed — try MyMemory per-segment
    try:
        from deep_translator import MyMemoryTranslator
        src = getattr(translator, 'source', 'auto')
        tgt = getattr(translator, 'target', 'en')
        mm = MyMemoryTranslator(source=src, target=tgt)
        results = []
        for t in texts:
            try:
                r = mm.translate(t)
                results.append(r if r else t)
            except Exception:
                results.append(t)
        print("[translate_batch] MyMemory fallback completed.")
        return results
    except Exception as fb_exc:
        print(f"[translate_batch] MyMemory fallback failed: {fb_exc}")

    # All backends failed — return original texts
    print("[translate_batch] All backends failed. Using original texts.")
    return list(texts)

# Configuration
import shutil as _shutil

# Try system ffmpeg first (Linux/production)
FFMPEG_EXE = _shutil.which('ffmpeg')

# Fallback to local Windows build (development only)
if not FFMPEG_EXE:
    FFMPEG_BIN_PATH = os.path.join(os.getcwd(),
                                   'ffmpeg-8.0.1-full_build', 'bin')
    _local = os.path.join(FFMPEG_BIN_PATH, 'ffmpeg.exe')
    if os.path.exists(_local):
        FFMPEG_EXE = _local

# Only mutate PATH if local build exists
if FFMPEG_EXE and 'ffmpeg-8.0.1' in FFMPEG_EXE:
    os.environ["PATH"] += os.pathsep + os.path.dirname(FFMPEG_EXE)

class VideoTranscriber:
    def __init__(self, model_size="base"):
        self.model_size = model_size
        self.model = None

    def load_model(self):
        if self.model is None:
            print(f"Loading Whisper model: {self.model_size}...")
            self.model = whisper.load_model(self.model_size)

    def extract_audio(self, video_path, audio_path):
        """Extract audio from video using local FFmpeg."""
        import shutil
        ffmpeg_exe = shutil.which('ffmpeg')
        if not ffmpeg_exe:
            local = os.path.join(os.getcwd(), 'ffmpeg-8.0.1-full_build', 'bin', 'ffmpeg.exe')
            if os.path.exists(local):
                ffmpeg_exe = local
        if not ffmpeg_exe:
            raise RuntimeError("FFmpeg not found in PATH")
        
        command = [
            ffmpeg_exe, '-y',
            '-i', video_path,
            '-vn',
            '-acodec', 'pcm_s16le',
            '-ar', '16000',
            '-ac', '1',
            audio_path
        ]
        try:
            subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"FFmpeg audio extraction failed: {e.stderr.decode('utf-8')}")
        
    def transcribe_and_translate(self, video_path, translate_to=None):
        """
        Full pipeline:
        1. Extract Audio
        2. Transcribe
        3. Translate (optional)
        4. Return segments
        """
        job_id = str(uuid.uuid4())
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        audio_filename = f"{base_name}_{job_id}.wav"
        audio_path = os.path.join(os.path.dirname(video_path), audio_filename)

        try:
            # 1. Extract Audio
            print(f"Extracting audio to {audio_path}")
            self.extract_audio(video_path, audio_path)

            # 2. Transcribe
            self.load_model()
            print("Transcribing audio...")
            result = self.model.transcribe(audio_path)
            
            origin_segments = result.get('segments', [])
            processed_segments = []

            # Setup translator if needed
            translator = None
            if translate_to:
                translator = GoogleTranslator(source='auto', target=translate_to)

            # 3. Process Segments
            no_internet_hit = False
            for i, seg in enumerate(origin_segments):
                text = seg['text'].strip()
                translated_text = None

                if translator and not no_internet_hit:
                    try:
                        translated_text = _translate_with_retry(translator, text)
                    except NoInternetError as exc:
                        print(f"[transcribe] No internet on segment {i + 1}: {exc}")
                        no_internet_hit = True  # stop attempting; use original for rest
                    except Exception as exc:
                        print(f"[transcribe] Translation error on segment {i + 1}: {exc}")

                processed_segments.append({
                    "id": i + 1,
                    "start": seg['start'],
                    "end": seg['end'],
                    "original_text": text,
                    "text": translated_text if translated_text else text,
                    "translated_text": translated_text,
                })

            # 4. Return segments + metadata so the route can surface no_internet
            return {"segments": processed_segments, "no_internet": no_internet_hit}

        finally:
            # Cleanup audio file
            if os.path.exists(audio_path):
                os.remove(audio_path)

    def translate_segments(self, segments, target_lang):
        """
        Translate all segments to *target_lang*, retrying on transient network
        errors. Raises NoInternetError if no internet connectivity is detected.
        """
        if not target_lang:
            return segments

        translator = GoogleTranslator(source='auto', target=target_lang)
        texts_to_translate = [s['text'] for s in segments]

        # ── Batch path (faster for large lists) ──────────────────────────────
        try:
            chunk_size = 50
            results = []
            for i in range(0, len(texts_to_translate), chunk_size):
                chunk = texts_to_translate[i:i + chunk_size]
                translated_chunk = _translate_batch_with_retry(translator, chunk)
                results.extend(translated_chunk)

            translated_segments = []
            for i, seg in enumerate(segments):
                new_seg = seg.copy()
                new_seg['text'] = results[i]
                new_seg['translated_text'] = results[i]
                translated_segments.append(new_seg)
            return translated_segments

        except NoInternetError:
            raise  # propagate so the route can return a 503

        except Exception as exc:
            print(f"[translate_segments] Batch path failed ({exc}). Falling back to sequential.")

        # ── Sequential fallback (per-segment with retry) ──────────────────────
        translated_segments = []
        for seg in segments:
            new_seg = seg.copy()
            try:
                res = _translate_with_retry(translator, seg['text'])
                new_seg['text'] = res
                new_seg['translated_text'] = res
            except NoInternetError:
                raise  # propagate
            except Exception as exc:
                print(f"[translate_segments] Sequential fallback failed for one segment: {exc}")
            translated_segments.append(new_seg)

        return translated_segments

# Global instance
transcriber = VideoTranscriber()
