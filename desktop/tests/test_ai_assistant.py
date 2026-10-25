"""Tests for the OpenRouter assistant, with no network involved.

The transport is injected, so every failure mode that matters can be produced on
demand: a rate limit, a dead upstream, a bad key, a key with no credits, a stream
that dies halfway, and a stream split across chunk boundaries.

The behaviour being pinned is the honesty of the answer:

* a reply from the fallback model is *labelled* as the fallback;
* a bad key or an empty balance is never masked by falling back;
* text that arrived before a failure is not thrown away, but is marked partial;
* the key never appears in an error message or a transcript.

Run with:  python -m unittest desktop.tests.test_ai_assistant -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from desktop.core import ai_assistant as ai
from desktop.core.ai_assistant import (
    AUTH, CREDITS, NETWORK, RATE_LIMIT, UPSTREAM,
    Answer, AssistantClient, AssistantError, ChatStore, KeyStore, mask_key,
)


def sse(*chunks: dict) -> list:
    """Encode chat-completion chunks the way OpenRouter streams them."""
    lines = [b"\n"]
    for chunk in chunks:
        lines.append(("data: " + json.dumps(chunk) + "\n\n").encode("utf-8"))
    lines.append(b"data: [DONE]\n\n")
    return lines


def text_chunk(text: str, model: str = "google/gemma-3-4b-it") -> dict:
    return {"model": model, "choices": [{"index": 0, "delta": {"content": text}}]}


class FakeTransport:
    """Returns a scripted response per call, and records what was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers),
                           "body": json.loads(body.decode("utf-8")), "timeout": timeout})
        if not self.responses:
            raise AssertionError("transport called more times than scripted")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return iter(response)


class SuccessfulReplyTest(unittest.TestCase):
    def test_primary_model_streams_and_names_itself(self):
        transport = FakeTransport(sse(text_chunk("Hello"), text_chunk(" world")))
        client = AssistantClient("sk-or-v1-test", transport=transport)

        deltas = []
        answer = client.chat([{"role": "user", "content": "hi"}],
                             on_delta=deltas.append)

        self.assertEqual(answer.text, "Hello world")
        self.assertEqual(answer.model, "google/gemma-3-4b-it")
        self.assertFalse(answer.used_fallback)
        self.assertFalse(answer.partial)
        self.assertEqual(deltas, ["Hello", " world"])

    def test_request_is_a_streaming_chat_completion_with_attribution(self):
        transport = FakeTransport(sse(text_chunk("ok")))
        AssistantClient("sk-or-v1-test", transport=transport).chat(
            [{"role": "user", "content": "hi"}])
        call = transport.calls[0]
        self.assertTrue(call["url"].endswith("/chat/completions"))
        self.assertTrue(call["body"]["stream"])
        self.assertEqual(call["body"]["model"], "google/gemma-3-4b-it")
        self.assertEqual(call["headers"]["X-Title"], "DroidForenX")
        self.assertIn("Bearer sk-or-v1-test", call["headers"]["Authorization"])

    def test_usage_is_captured_from_the_final_chunk(self):
        chunks = sse(
            text_chunk("done"),
            {"choices": [{"delta": {}}], "usage": {"total_tokens": 42, "cost": 0.0001}},
        )
        answer = AssistantClient("k", transport=FakeTransport(chunks)).chat(
            [{"role": "user", "content": "hi"}])
        self.assertEqual(answer.usage.get("total_tokens"), 42)


class FallbackTest(unittest.TestCase):
    def test_rate_limit_falls_back_and_says_so(self):
        transport = FakeTransport(
            AssistantError(RATE_LIMIT, "HTTP 429"),
            sse(text_chunk("from the small model",
                           model="meta-llama/llama-3.1-8b-instruct")),
        )
        answer = AssistantClient("k", transport=transport).chat(
            [{"role": "user", "content": "hi"}])

        self.assertEqual(answer.model, "meta-llama/llama-3.1-8b-instruct")
        self.assertTrue(answer.used_fallback)
        self.assertEqual(answer.failures, [("google/gemma-3-4b-it", RATE_LIMIT)])
        self.assertEqual(len(transport.calls), 2)

    def test_upstream_error_falls_back(self):
        transport = FakeTransport(AssistantError(UPSTREAM, "HTTP 502"),
                                  sse(text_chunk("ok")))
        answer = AssistantClient("k", transport=transport).chat(
            [{"role": "user", "content": "hi"}])
        self.assertTrue(answer.used_fallback)

    def test_bad_key_is_not_masked_by_a_fallback(self):
        transport = FakeTransport(AssistantError(AUTH, "HTTP 401"))
        with self.assertRaises(AssistantError) as caught:
            AssistantClient("k", transport=transport).chat(
                [{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.kind, AUTH)
        self.assertEqual(len(transport.calls), 1, "must not try the next model")
        self.assertIn("API key", caught.exception.user_message)

    def test_no_credits_is_not_masked_by_a_fallback(self):
        transport = FakeTransport(AssistantError(CREDITS, "HTTP 402"))
        with self.assertRaises(AssistantError) as caught:
            AssistantClient("k", transport=transport).chat(
                [{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.kind, CREDITS)
        self.assertEqual(len(transport.calls), 1)
        self.assertIn("credits", caught.exception.user_message)

    def test_all_models_failing_reports_the_chain(self):
        transport = FakeTransport(AssistantError(RATE_LIMIT, "HTTP 429"),
                                  AssistantError(RATE_LIMIT, "HTTP 429"))
        with self.assertRaises(AssistantError) as caught:
            AssistantClient("k", transport=transport).chat(
                [{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.kind, RATE_LIMIT)
        self.assertEqual(len(transport.calls), 2)

    def test_missing_key_never_reaches_the_network(self):
        transport = FakeTransport()
        with self.assertRaises(AssistantError) as caught:
            AssistantClient("", transport=transport).chat(
                [{"role": "user", "content": "hi"}])
        self.assertEqual(caught.exception.kind, AUTH)
        self.assertEqual(transport.calls, [])


class PartialReplyTest(unittest.TestCase):
    def test_stream_dying_halfway_keeps_what_arrived_and_labels_it(self):
        def dying(url, headers, body, timeout):
            yield ("data: " + json.dumps(text_chunk("half an ")) + "\n\n").encode()
            raise AssistantError(NETWORK, "connection reset")

        answer = AssistantClient("k", transport=dying).chat(
            [{"role": "user", "content": "hi"}])
        self.assertEqual(answer.text, "half an ")
        self.assertTrue(answer.partial)

    def test_empty_stream_falls_back_rather_than_showing_nothing(self):
        transport = FakeTransport(sse(), sse(text_chunk("second model")))
        answer = AssistantClient("k", transport=transport).chat(
            [{"role": "user", "content": "hi"}])
        self.assertEqual(answer.text, "second model")
        self.assertTrue(answer.used_fallback)


class StreamingFormatTest(unittest.TestCase):
    def test_keepalives_and_blank_lines_are_ignored(self):
        lines = [b": OPENROUTER PROCESSING\n", b"\n",
                 ("data: " + json.dumps(text_chunk("ok")) + "\n").encode(),
                 b"data: [DONE]\n"]
        answer = AssistantClient("k", transport=FakeTransport(lines)).chat(
            [{"role": "user", "content": "hi"}])
        self.assertEqual(answer.text, "ok")

    def test_malformed_json_line_does_not_lose_the_reply(self):
        lines = [b"data: {not json}\n",
                 ("data: " + json.dumps(text_chunk("ok")) + "\n").encode(),
                 b"data: [DONE]\n"]
        answer = AssistantClient("k", transport=FakeTransport(lines)).chat(
            [{"role": "user", "content": "hi"}])
        self.assertEqual(answer.text, "ok")

    def test_provider_error_event_is_reported(self):
        lines = [("data: " + json.dumps({"error": {"message": "upstream boom"}}) + "\n").encode()]
        transport = FakeTransport(lines, sse(text_chunk("fallback")))
        answer = AssistantClient("k", transport=transport).chat(
            [{"role": "user", "content": "hi"}])
        self.assertTrue(answer.used_fallback)


class KeyMaskingTest(unittest.TestCase):
    def test_mask_hides_the_middle(self):
        masked = mask_key("sk-or-v1-abcdefghijklmnopqrstuvwxyz")
        self.assertTrue(masked.startswith("sk-or-v1-a"))
        self.assertTrue(masked.endswith("wxyz"))
        self.assertNotIn("ghijklmnopqrstu", masked)

    def test_short_or_empty_keys_do_not_leak(self):
        self.assertEqual(mask_key(""), "")
        self.assertEqual(mask_key("short"), "\u2026")


class KeyStoreTest(unittest.TestCase):
    def setUp(self):
        # The suite may itself be running with a key in the environment, which
        # correctly takes precedence over the file. These tests are about the
        # file, so the variable is parked for their duration.
        import os
        self._env = os.environ.pop("OPENROUTER_API_KEY", None)

    def tearDown(self):
        import os
        if self._env is not None:
            os.environ["OPENROUTER_API_KEY"] = self._env

    def test_round_trip_under_a_temp_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KeyStore(Path(tmp) / "assistant.key")
            store.save("sk-or-v1-roundtrip")
            self.assertEqual(store.load(), "sk-or-v1-roundtrip")
            store.clear()
            self.assertEqual(store.load(), "")

    def test_environment_wins_over_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KeyStore(Path(tmp) / "assistant.key")
            store.save("sk-or-v1-fromfile")
            import os
            old = os.environ.get("OPENROUTER_API_KEY")
            os.environ["OPENROUTER_API_KEY"] = "sk-or-v1-fromenv"
            try:
                self.assertEqual(store.load(), "sk-or-v1-fromenv")
            finally:
                if old is None:
                    os.environ.pop("OPENROUTER_API_KEY", None)
                else:
                    os.environ["OPENROUTER_API_KEY"] = old

    def test_the_file_is_not_plaintext_on_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = KeyStore(Path(tmp) / "assistant.key")
            store.save("sk-or-v1-plaintext-check")
            raw = store.path.read_bytes()
            if raw.startswith(b"sk-or-v1-"):
                self.skipTest("DPAPI unavailable on this platform")
            self.assertNotIn(b"plaintext-check", raw)


class ChatStoreTest(unittest.TestCase):
    def test_history_is_per_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatStore(Path(tmp))
            store.append({"role": "user", "content": "about case A"}, case_id="CASE-1")
            store.append({"role": "assistant", "content": "answer A"}, case_id="CASE-1")
            store.append({"role": "user", "content": "about case B"}, case_id="CASE-2")

            self.assertEqual(len(store.load("CASE-1")), 2)
            self.assertEqual(len(store.load("CASE-2")), 1)
            self.assertEqual(store.load("CASE-3"), [])

    def test_clearing_one_case_leaves_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatStore(Path(tmp))
            store.append({"role": "user", "content": "a"}, case_id="A")
            store.append({"role": "user", "content": "b"}, case_id="B")
            store.clear("A")
            self.assertEqual(store.load("A"), [])
            self.assertEqual(len(store.load("B")), 1)

    def test_a_case_id_cannot_escape_the_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatStore(Path(tmp))
            path = store.path_for("../../evil")
            self.assertEqual(path.parent, Path(tmp))

    def test_unassigned_chats_have_their_own_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatStore(Path(tmp))
            store.append({"role": "user", "content": "scratch"})
            self.assertEqual(store.load(""), [{"role": "user", "content": "scratch",
                                               "ts": store.load("")[0]["ts"]}])

    def test_corrupt_lines_are_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ChatStore(Path(tmp))
            store.append({"role": "user", "content": "keep me"}, case_id="C")
            with open(store.path_for("C"), "a", encoding="utf-8") as handle:
                handle.write("{not json}\n")
            self.assertEqual(len(store.load("C")), 1)


if __name__ == "__main__":
    unittest.main()
