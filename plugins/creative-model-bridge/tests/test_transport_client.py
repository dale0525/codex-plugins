from __future__ import annotations

import json
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.request

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "mcp"))

from bridge import BridgeError  # noqa: E402
from transport_client import ResponsesClient  # noqa: E402


class Response:
    def __init__(self, chunks: list[bytes], content_type: str = "text/event-stream", headers: dict[str, str] | None = None) -> None:
        self.chunks = list(chunks)
        self.status = 200
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.closed = False

    def read(self, size: int | None = None) -> bytes:
        if not self.chunks:
            return b""
        if size is None:
            return b"".join(self.chunks)
        return self.chunks.pop(0)

    def close(self) -> None:
        self.closed = True


class Opener:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.requests: list[object] = []

    def __call__(self, request: object, timeout: float) -> Response:
        return self.open(request, timeout)

    def open(self, request: object, timeout: float) -> Response:
        self.requests.append(request)
        return self.response


class Provider:
    base_url = "https://provider.test/v1"


def client(opener: Opener) -> ResponsesClient:
    return ResponsesClient(
        Provider(),
        "test-secret",
        opener,
        error_factory=BridgeError,
        failure_factory=lambda message, error, phase, enabled: BridgeError(message),
        response_diagnostic=lambda prefix, payload, **kwargs: prefix,
        user_agent="creative-model-bridge/test",
    )


def frame(payload: object, *, event: str | None = None) -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return (prefix + "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


class ChatStreamingTests(unittest.TestCase):
    def test_no_redirect_opener_is_created_at_request_boundary(self) -> None:
        import transport_client

        original_build_opener = urllib.request.build_opener
        with patch.object(urllib.request, "build_opener", wraps=original_build_opener) as build_opener:
            transport_client = importlib.reload(transport_client)
            # Importing the module must not construct an opener before server
            # startup has initialized SSL_CERT_FILE.
            build_opener.assert_not_called()

            response = Response([b'{"data":[{"id":"model"}]}'], content_type="application/json")
            opener = Opener(response)
            build_opener.return_value = opener
            request = urllib.request.Request("https://provider.test/v1/models")
            opened = transport_client._open_without_redirects(request, timeout=3.0)

        self.assertIs(opened, response)
        build_opener.assert_called_once()
        handlers = build_opener.call_args.args
        self.assertEqual(len(handlers), 1)
        self.assertIsInstance(handlers[0], transport_client._NoRedirectHandler)
        self.assertIsNone(handlers[0].redirect_request(request, None, 302, "Found", {}, "https://other.test"))
        self.assertEqual(opener.requests, [request])

    def test_split_utf8_lf_crlf_comments_multidata_reasoning_tool_and_usage_tail(self) -> None:
        first = b": keepalive\r\n\r\nevent: message\r\ndata: {\"id\":\"chat-1\",\"choices\":[{\"delta\":{\"reasoning_content\":\"hidden\",\"content\":\"\xe4\xb8\x80\"},\"finish_reason\":null}]}\r\n\r\n"
        second = b"data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"id\":\"tool\"}],\"content\":\"\xe4\xb8\x8b\"},\"finish_reason\":\"stop\"}]}\n\n"
        response = Response([first[:97], first[97:] + second[:31], second[31:] + b"data: {\"choices\":[] ,\"usage\":{\"total_tokens\":9}}\n\n", b"data: [DONE]\n\n"])
        result = client(Opener(response)).chat_completions({"model": "m", "messages": [], "stream": True})
        self.assertEqual(result, ("一下", {"total_tokens": 9}, "chat-1"))
        self.assertTrue(response.closed)

    def test_natural_eof_requires_finish_reason_and_done_ignores_late_frames(self) -> None:
        finished = frame({"id": "eof", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})
        result = client(Opener(Response([finished]))).chat_completions({})
        self.assertEqual(result[0], "ok")
        unfinished = frame({"id": "eof", "choices": [{"delta": {"content": "partial"}, "finish_reason": None}]})
        with self.assertRaises(BridgeError):
            client(Opener(Response([unfinished]))).chat_completions({})
        done = frame({"id": "done", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}) + b"data: [DONE]\n\n" + b"data: not-json\n\n"
        self.assertEqual(client(Opener(Response([done]))).chat_completions({})[0], "ok")

    def test_crlf_split_at_chunk_boundary_and_chunk_id_precedes_header(self) -> None:
        payload = ("data: " + json.dumps({"id": "chunk-id", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}) + "\r\n\r\n").encode()
        split = payload.index(b"\r\n") + 1
        response = Response([payload[:split], payload[split:] + b"data: [DONE]\r\n\r\n"], headers={"x-request-id": "header-id"})
        self.assertEqual(client(Opener(response)).chat_completions({})[2], "chunk-id")
        no_id = frame({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})
        response = Response([no_id], headers={"x-request-id": "header-id"})
        self.assertEqual(client(Opener(response)).chat_completions({})[2], "header-id")

    def test_choice_index_zero_is_selected_even_when_choices_are_out_of_order(self) -> None:
        payload = {
            "id": "indexed",
            "choices": [
                {"index": 1, "delta": {"content": "WRONG"}, "finish_reason": "stop"},
                {"index": 0, "delta": {"content": "RIGHT"}, "finish_reason": "stop"},
            ],
        }
        body = frame(payload) + b"data: [DONE]\n\n"
        self.assertEqual(client(Opener(Response([body]))).chat_completions({})[0], "RIGHT")

    def test_only_nonzero_choice_cannot_complete_or_supply_text(self) -> None:
        payload = {"id": "nonzero", "choices": [{"index": 1, "delta": {"content": "WRONG"}, "finish_reason": "stop"}]}
        with self.assertRaises(BridgeError):
            client(Opener(Response([frame(payload) + b"data: [DONE]\n\n"]))).chat_completions({})

    def test_non_string_finish_reason_is_malformed_and_missing_index_single_choice_is_compatible(self) -> None:
        invalid = {"id": "bad-finish", "choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": False}]}
        with self.assertRaises(BridgeError):
            client(Opener(Response([frame(invalid)]))).chat_completions({})
        compatible = {"id": "single", "choices": [{"delta": {"content": "single"}, "finish_reason": "stop"}]}
        self.assertEqual(client(Opener(Response([frame(compatible)]))).chat_completions({})[0], "single")

    def test_duplicate_finish_usage_only_top_level_and_event_errors_are_safe(self) -> None:
        body = frame({"id": "dup", "choices": [{"delta": {"content": "A"}, "finish_reason": "stop"}]})
        body += frame({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        body += frame({"choices": [], "usage": {"prompt_tokens": 1}}) + b"data: [DONE]\n\n"
        self.assertEqual(client(Opener(Response([body]))).chat_completions({})[0:2], ("A", {"prompt_tokens": 1}))
        for payload in ({"error": {"message": "provider-secret"}}, {"choices": [{"delta": {"content": "x"}}]}):
            response = Response([frame(payload)])
            with self.assertRaises(BridgeError) as error:
                client(Opener(response)).chat_completions({})
            self.assertNotIn("provider-secret", str(error.exception))
        with self.assertRaises(BridgeError):
            client(Opener(Response([b"event: error\n\n"]))).chat_completions({})

    def test_malformed_utf8_and_json_and_tool_only_are_rejected(self) -> None:
        for chunks in ([b"data: \xff\n\n"], [b"data: {bad}\n\n"]):
            with self.assertRaises(BridgeError):
                client(Opener(Response(list(chunks)))).chat_completions({})
        tool_only = frame({"id": "tool", "choices": [{"delta": {"tool_calls": [{"id": "x"}]}, "finish_reason": "tool_calls"}]}) + b"data: [DONE]\n\n"
        with self.assertRaises(BridgeError):
            client(Opener(Response([tool_only]))).chat_completions({})

    def test_non_event_stream_json_fallback_is_verbatim_and_uses_id_usage(self) -> None:
        payload = {"id": "json-1", "choices": [{"message": {"content": "{\"verbatim\": true}"}, "finish_reason": "stop"}], "usage": {"total_tokens": 4}}
        response = Response([json.dumps(payload).encode()], content_type="application/json")
        result = client(Opener(response)).chat_completions({})
        self.assertEqual(result, ('{"verbatim": true}', {"total_tokens": 4}, "json-1"))
        for message in ({"tool_calls": [{"id": "x"}]}, {"reasoning_content": "hidden"}, {"refusal": "no"}):
            bad = {"choices": [{"message": message}]}
            with self.assertRaises(BridgeError):
                client(Opener(Response([json.dumps(bad).encode()], content_type="application/json"))).chat_completions({})


if __name__ == "__main__":
    unittest.main()
