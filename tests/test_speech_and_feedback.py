import base64
import json
import unittest
from unittest.mock import patch

import app


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit=-1):
        return self.payload if limit < 0 else self.payload[:limit]


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.request = None
        self.timeout = None

    def open(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return FakeResponse(self.payload)


class SpeechAndFeedbackTests(unittest.TestCase):
    def audio_data(self, mime="audio/webm", content=b"recorded-child-voice"):
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def test_decode_audio_accepts_supported_webm(self):
        mime, encoded = app.decode_audio_data(self.audio_data(), "audio/webm")
        self.assertEqual(mime, "audio/webm")
        self.assertEqual(base64.b64decode(encoded), b"recorded-child-voice")

    def test_decode_audio_rejects_mime_mismatch(self):
        with self.assertRaisesRegex(ValueError, "unsupported audio format"):
            app.decode_audio_data(self.audio_data("audio/webm"), "audio/wav")

    def test_decode_audio_rejects_invalid_base64(self):
        with self.assertRaisesRegex(ValueError, "invalid audio encoding"):
            app.decode_audio_data("data:audio/webm;base64,not-valid%%%", "audio/webm")

    def test_decode_audio_rejects_empty_and_oversized_audio(self):
        with self.assertRaisesRegex(ValueError, "empty or too large"):
            app.decode_audio_data(self.audio_data(content=b""), "audio/webm")
        oversized = self.audio_data(content=b"x" * (app.MAX_AUDIO_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "empty or too large"):
            app.decode_audio_data(oversized, "audio/webm")

    def test_transcribe_speech_uses_configured_model_and_audio_payload(self):
        opener = FakeOpener({"choices": [{"message": {"content": "为什么月亮跟着我走？"}}]})
        runtime = ({"http_model_id": "qwen3-asr-flash-test", "timeout_seconds": 8}, "secret", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        with patch("app.speech_runtime", return_value=runtime), \
             patch("app.validate_outbound_url", return_value=True), \
             patch("app.build_opener", return_value=opener):
            transcript = app.transcribe_speech(self.audio_data(), "audio/webm")
        self.assertEqual(transcript, "为什么月亮跟着我走？")
        payload = json.loads(opener.request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "qwen3-asr-flash-test")
        audio = payload["messages"][0]["content"][0]["input_audio"]
        self.assertTrue(audio["data"].startswith("data:audio/webm;base64,"))
        self.assertNotIn("format", audio)
        self.assertEqual(payload["asr_options"]["language"], "zh")
        self.assertEqual(opener.request.headers["Authorization"], "Bearer secret")

    def test_synthesize_speech_uses_warm_child_voice_configuration(self):
        opener = FakeOpener({"output": {"audio": {"url": "https://example.oss-cn-beijing.aliyuncs.com/voice.wav"}}})
        runtime = ({
            "http_model_id": "qwen3-tts-instruct-flash",
            "voice": "Cherry",
            "instructions": "阳光、亲切、自然地讲给孩子听。",
            "timeout_seconds": 8,
        }, "secret", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        with patch("app.speech_runtime", return_value=runtime), \
             patch("app.validate_outbound_url", return_value=True), \
             patch("app.build_opener", return_value=opener):
            result = app.synthesize_speech("月亮离我们很远，所以看起来像跟着你。")
        self.assertEqual(result["voice"], "Cherry")
        payload = json.loads(opener.request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "qwen3-tts-instruct-flash")
        self.assertEqual(payload["input"]["voice"], "Cherry")
        self.assertIn("自然", payload["input"]["instructions"])
        self.assertEqual(result["audioUrl"], "https://example.oss-cn-beijing.aliyuncs.com/voice.wav")

    def test_generated_audio_url_rejects_non_aliyun_or_credentials(self):
        self.assertEqual(app.validate_generated_audio_url("http://example.aliyuncs.com/a.wav"), "https://example.aliyuncs.com/a.wav")
        self.assertEqual(app.validate_generated_audio_url("https://evil.example/a.wav"), "")
        self.assertEqual(app.validate_generated_audio_url("https://user:pass@example.aliyuncs.com/a.wav"), "")

    def test_extract_synthesized_audio_accepts_nested_base64(self):
        encoded = base64.b64encode(b"short-mp3-audio").decode("ascii")
        result = app.extract_synthesized_audio({"output": {"audio": {"data": encoded, "mime_type": "audio/mpeg"}}})
        self.assertEqual(result, f"data:audio/mpeg;base64,{encoded}")

    def test_feedback_prompts_require_real_reframing(self):
        self.assertIn("另一个角度", app.feedback_instruction("confused"))
        self.assertIn("不要重复", app.feedback_instruction("confused"))
        self.assertIn("明确指出它说明了什么", app.feedback_instruction("example"))
        self.assertIn("多补一层原因", app.feedback_instruction("why"))

    def test_answer_repetition_ratio_detects_repeated_answer(self):
        previous = "月亮离我们很远。你走几步，它看起来位置变化很小。"
        self.assertGreater(app.answer_repetition_ratio(previous, previous), 0.99)
        reframed = "想象你在车里看远山：近处的树很快退后，远山却像一直陪着你。月亮比远山还远。"
        self.assertLess(app.answer_repetition_ratio(reframed, previous), 0.72)

    def test_why_feedback_rejects_shallow_repetition(self):
        previous = "月亮离我们非常远。你走几步，它看起来几乎没变。"
        plan = app.heuristic_question_plan("为什么月亮好像跟着我走？", "ask")
        plan.update({
            "planValidation": {"valid": True},
            "planSemanticReview": {"passed": True},
            "feedbackMode": "why",
            "previousAnswer": previous,
        })
        candidate = app.reference_answer_candidate("因为月亮离地球特别远。你走几步，它的位置看起来几乎没变。", plan)
        review = app.local_quality_review(candidate, plan, app.child_level_alignment({"age": 5}, feedback="why"))
        self.assertFalse(review["passed"])
        self.assertTrue(any("过于相似" in item for item in review["violations"]))

    def test_why_generation_prompt_requires_a_deeper_reason(self):
        prompt = app.generation_system_prompt()
        self.assertIn("多补一层原因", app.feedback_instruction("why"))
        self.assertIn("补充上一轮没有讲出的下一层原因", prompt)

    def test_quality_review_rejects_consecutive_fragment_sentences(self):
        plan = app.heuristic_question_plan("影子为什么会动？", "ask")
        plan.update({"planStatus": "ready", "planValidation": {"valid": True}, "planSemanticReview": {"passed": True}})
        candidate = app.reference_answer_candidate("因为光。被挡住。影子就动。", plan)
        review = app.local_quality_review(candidate, plan, app.child_level_alignment({"age": 5}))
        self.assertTrue(review["languageEvidence"]["consecutiveFragments"])
        self.assertTrue(any("碎片句" in item for item in review["violations"]))


if __name__ == "__main__":
    unittest.main()
