import base64
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

import office_ask as ask
import voice


class VoiceTransportTests(unittest.TestCase):
    """Voice is a transport on Ask: the proof is that a dictation lands as the
    same rows a typed send creates, once, and the reply comes back as audio."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database = pathlib.Path(temp.name) / "ask.sqlite3"
        self.path_patch = patch.object(ask, "path", return_value=self.database)
        self.models_patch = patch.object(ask, "models", return_value={
            "items": [{"id": "model-a"}], "default": "model-a"})
        self.path_patch.start()
        self.models_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.models_patch.stop)
        # Spoken-audio cache lives in a temp dir for the whole class.
        self.cache_patch = patch.dict(os.environ, {
            "OFFICE_VOICE_CACHE": str(pathlib.Path(temp.name) / "voice-cache")})
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.answerer = None
        ask.read()  # create the schema before any concurrency: two threads
        # racing connect() on a nonexistent file fight over WAL + CREATE.

    def _start_answerer(self, reply_id, text="Nothing is blocked on you."):
        def run():
            deadline = time.time() + 10
            while time.time() < deadline:
                with ask.connect() as db:
                    row = db.execute("SELECT status FROM messages WHERE id=?",
                                     (reply_id,)).fetchone()
                    if row:
                        ask._update_message(db, reply_id, text=text, status="completed")
                        db.commit()
                        return
                time.sleep(0.05)
        self.answerer = threading.Thread(target=run, daemon=True)
        self.answerer.start()

    def _converse(self, request_id):
        audio = base64.b64encode(b"fake dictation bytes").decode()
        with patch.object(voice, "transcribe", return_value="Is anything blocked on me?") as heard, \
             patch.object(voice, "synthesize", return_value=(b"RIFF....", "stub")) as spoken:
            # The answerer needs the reply id, which only exists after send;
            # converse waits for it, so complete whatever reply appears. One
            # connection, human poll rate: connect() sets WAL mode every call
            # and a 20/s poller starves send() of the lock.
            def complete_any():
                deadline = time.time() + 10
                db = ask.connect()
                try:
                    while time.time() < deadline:
                        row = db.execute(
                            "SELECT id FROM messages WHERE role=? AND status IN (?,?)"
                            " ORDER BY id LIMIT 1", ("office", "queued", "working")).fetchone()
                        if row:
                            ask._update_message(db, row["id"],
                                                text="Nothing is blocked on you.",
                                                status="completed")
                            db.commit()
                            return
                        time.sleep(0.25)
                finally:
                    db.close()
            threading.Thread(target=complete_any, daemon=True).start()
            result = voice.converse({"audio_base64": audio, "format": "wav",
                                     "request_id": request_id, "model": "model-a"},
                                    wait_s=10)
        return result, heard, spoken

    def test_dictation_enters_the_ask_session_and_the_reply_is_spoken(self):
        result, heard, spoken = self._converse("voice-request-0001")
        self.assertEqual(result["transcript"], "Is anything blocked on me?")
        self.assertEqual(result["reply_text"], "Nothing is blocked on you.")
        self.assertEqual(base64.b64decode(result["audio_base64"]), b"RIFF....")
        state = ask.read()
        user = [m for m in state["messages"] if m["role"] == "user"
                and m["text"] == "Is anything blocked on me?"]
        self.assertEqual(len(user), 1)

    def test_a_retried_dictation_never_double_posts(self):
        self._converse("voice-request-0002")
        self._converse("voice-request-0002")
        state = ask.read()
        user = [m for m in state["messages"] if m["role"] == "user"
                and m["request_id"] == "voice-request-0002"]
        self.assertEqual(len(user), 1)

    def test_speak_rejects_empty_and_speaks_long_replies_in_full(self):
        # Aria's ruling (2026-10-14): on-demand audio is the WHOLE answer.
        # The old 4000-char truncation ("The rest is in the chat.") is gone;
        # a long reply reaches the provider complete, chunked underneath.
        with self.assertRaises(ValueError):
            voice.speak({"text": "  "})
        long_text = "One. " * 1500  # 7500 chars: past the old cap
        with patch.object(voice, "_speak_say", return_value=b"RIFF") as say:
            out = voice.speak({"text": long_text})
        spoken_text = say.call_args[0][0]
        self.assertEqual(spoken_text, long_text.strip())
        self.assertNotIn("The rest is in the chat", spoken_text)
        self.assertFalse(out["cached"])
        self.assertEqual(base64.b64decode(out["audio_base64"]), b"RIFF")

    def test_speak_caches_generated_audio_per_answer(self):
        with patch.object(voice, "_speak_say", return_value=b"RIFF") as say:
            first = voice.speak({"text": "Cache me once."})
            second = voice.speak({"text": "Cache me once."})
        self.assertEqual(say.call_count, 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(first["audio_base64"], second["audio_base64"])

    def test_chunks_cover_the_whole_text_within_the_limit(self):
        text = " ".join(f"Sentence number {n} is here." for n in range(60))
        chunks = voice._chunks(text)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= voice.SPEAK_CHUNK_CHARS for c in chunks))
        self.assertEqual(" ".join(chunks), text)

    def test_transcribe_refuses_bad_shape_before_any_model_loads(self):
        with self.assertRaises(ValueError):
            voice.transcribe(b"", "wav")
        with self.assertRaises(ValueError):
            voice.transcribe(b"x" * (voice.MAX_AUDIO_BYTES + 1), "wav")
        with self.assertRaises(ValueError):
            voice.transcribe(b"data", "exe")

    def test_converse_refuses_non_base64_audio(self):
        with self.assertRaises(ValueError):
            voice.converse({"audio_base64": "!!! not base64 !!!"})


if __name__ == "__main__":
    unittest.main()
