from __future__ import annotations

import unittest

from server.db import _sql_statements
from server.judge import chat_model_for, looks_like_speech_model, mock_judge
from server.speech import mime_to_audio_meta
from server.tts import audio_url_candidates, explain_tts_failure, qwen_business_error, sniff_audio_mime, speech_url


class LogicTest(unittest.TestCase):
    def test_schema_is_owned_by_migrations(self) -> None:
        from server.db import MIGRATIONS_DIR, SCHEMA_PATH, _sql_statements

        text = SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("schema_migrations", text)
        stmts = _sql_statements(text)
        self.assertEqual(stmts, ["SELECT 1"])
        init_sql = (MIGRATIONS_DIR / "001_init.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS users", init_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS questions", init_sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS resumes", init_sql)
        self.assertGreater(len(_sql_statements(init_sql)), 20)

    def test_password_hash_roundtrip(self) -> None:
        from server.auth import hash_password, verify_password

        extra = hash_password("secret-1")
        self.assertTrue(verify_password("secret-1", extra))
        self.assertFalse(verify_password("secret-2", extra))

    def test_pick_next_parent_follows_path_and_preference(self) -> None:
        from server.recommend import pick_next_parent, preferred_parent_ids

        parents = [
            {"id": "a1", "topicId": "t1", "roles": ["Go"]},
            {"id": "a2", "topicId": "t1", "roles": ["Java 后端"]},
            {"id": "b1", "topicId": "t2", "roles": ["Java 后端"]},
        ]
        self.assertEqual(
            pick_next_parent(["t1", "t2"], parents, set())["id"],
            "a1",
        )
        self.assertEqual(
            pick_next_parent(["t1", "t2"], parents, {"a1", "a2"})["id"],
            "b1",
        )
        preferred = preferred_parent_ids("transition", parents)
        self.assertEqual(preferred, {"a2", "b1"})
        self.assertEqual(
            pick_next_parent(["t1", "t2"], parents, set(), preferred)["id"],
            "a2",
        )
        self.assertEqual(preferred_parent_ids("experienced", parents), set())
        self.assertIsNone(pick_next_parent(["t1"], parents, {"a1", "a2"}))

    def test_speech_mime(self) -> None:
        self.assertEqual(mime_to_audio_meta("audio/pcm"), {"mime": "audio/wav", "ext": "wav"})
        self.assertEqual(mime_to_audio_meta("audio/x-m4a"), {"mime": "audio/mp4", "ext": "m4a"})
        self.assertEqual(mime_to_audio_meta("audio/webm;codecs=opus"), {"mime": "audio/webm", "ext": "webm"})

    def test_judge_speech_model_remap(self) -> None:
        self.assertTrue(looks_like_speech_model("qwen3-tts-instruct-flash"))
        self.assertFalse(looks_like_speech_model("qwen-plus"))
        self.assertEqual(
            chat_model_for(
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "qwen3-tts-instruct-flash",
                "qwen",
            ),
            "qwen-plus",
        )

    def test_mock_judge_short(self) -> None:
        result = mock_judge("太短", "结论", [{"heading": "机制", "body": "失败模式"}], "", "")
        self.assertEqual(result["verdict"], "挂")
        self.assertEqual(result["mode"], "mock")

    def test_tts_helpers(self) -> None:
        self.assertIsNone(qwen_business_error({"status_code": 200, "code": "", "message": "", "output": {}}))
        self.assertIn(
            "model not found",
            qwen_business_error({"status_code": 200, "code": "InvalidModel", "message": "model not found"}) or "",
        )
        self.assertEqual(
            audio_url_candidates("http://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.wav"),
            [
                "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/a.wav",
            ],
        )
        self.assertEqual(
            speech_url("https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "https://dashscope.aliyuncs.com/compatible-mode/v1/audio/speech",
        )
        wav = bytes([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x41, 0x56, 0x45])
        self.assertEqual(sniff_audio_mime(wav), "audio/wav")
        self.assertIn("API Key 无效", explain_tts_failure("InvalidApiKey"))

    def test_resume_file_kind(self) -> None:
        from server.resume_files import file_kind, reject_reason, RESUME_MAX_BYTES

        self.assertEqual(file_kind("a.PDF", "application/pdf"), "pdf")
        self.assertEqual(
            file_kind("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "docx",
        )
        self.assertEqual(file_kind("a.jpg", "image/jpeg"), "jpeg")
        self.assertEqual(file_kind("a.png", ""), "png")
        self.assertEqual(file_kind("a.webp", "image/webp"), "webp")
        self.assertEqual(file_kind("old.doc", "application/msword"), "doc")
        self.assertEqual(file_kind("x.bin", "application/octet-stream"), "unknown")
        self.assertEqual(RESUME_MAX_BYTES, 8 * 1024 * 1024)
        self.assertIn("docx", reject_reason("cv.doc", "application/msword", 100) or "")
        self.assertIn("8MB", reject_reason("a.pdf", "application/pdf", 9 * 1024 * 1024) or "")
        self.assertIsNone(reject_reason("a.pdf", "application/pdf", 12))

    def test_resume_parse_status(self) -> None:
        from server.resume_parse import classify_parse

        self.assertEqual(classify_parse("", [{"name": "X", "responsibilities": "做了"}]), "failed")
        self.assertEqual(classify_parse("有字", []), "needs_review")
        self.assertEqual(
            classify_parse("有字", [{"name": "X", "responsibilities": ""}]),
            "needs_review",
        )
        self.assertEqual(
            classify_parse("有字", [{"name": "网关", "responsibilities": "我做鉴权"}]),
            "parsed",
        )
        self.assertEqual(
            classify_parse(
                "有字",
                [
                    {"name": "网关", "responsibilities": "我做鉴权"},
                    {"name": "空项目", "responsibilities": ""},
                ],
            ),
            "needs_review",
        )

    def test_resume_file_roundtrip(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from server.resume_files import read_stored, write_stored

        data = b"%PDF-fake"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "file.bin"
            with patch("server.resume_files.RESUME_FILE_KEY", ""):
                write_stored(path, data)
                self.assertEqual(read_stored(path), data)
            with patch("server.resume_files.RESUME_FILE_KEY", "unit-test-secret"):
                write_stored(path, data)
                raw = path.read_bytes()
                self.assertNotEqual(raw, data)
                self.assertEqual(read_stored(path), data)

    def test_extract_pdf_and_docx(self) -> None:
        import io

        from pypdf import PdfWriter
        from docx import Document

        from server.resume_parse import extract_pdf_text, extract_docx_text

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        buf = io.BytesIO()
        writer.write(buf)
        self.assertIsInstance(extract_pdf_text(buf.getvalue()), str)

        doc = Document()
        doc.add_paragraph("爱连健康网关")
        out = io.BytesIO()
        doc.save(out)
        self.assertIn("爱连", extract_docx_text(out.getvalue()))

    def test_extract_image_uses_vision(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, patch

        from server.resume_parse import extract_image_text

        with patch("server.resume_parse._vision_ocr", new_callable=AsyncMock, return_value="  项目：收银台  "):
            self.assertEqual(asyncio.run(extract_image_text(b"\xff\xd8", "image/jpeg")), "项目：收银台")

    def test_normalize_structured_archive(self) -> None:
        from server.resume_parse import normalize_structured

        raw = {
            "profile": {
                "fullName": "张三",
                "yearsExperience": "8",
                "currentTitle": "Java",
                "targetTitle": "Agent",
                "summary": "转岗",
                "skills": ["Java", 1, ""],
            },
            "jobs": [{"company": "爱连", "jobTitle": "后端", "isCurrent": True, "sortOrder": 0}],
            "projects": [
                {
                    "name": "收银台",
                    "responsibilities": "写网关",
                    "outcomes": "延迟下降",
                    "techStack": ["Java"],
                    "jobIndex": 0,
                }
            ],
        }
        out = normalize_structured(raw)
        self.assertEqual(out["profile"]["full_name"], "张三")
        self.assertEqual(out["profile"]["years_experience"], 8)
        self.assertEqual(out["profile"]["skills"], ["Java"])
        self.assertEqual(out["jobs"][0]["company"], "爱连")
        self.assertEqual(out["projects"][0]["name"], "收银台")
        self.assertEqual(out["projects"][0]["_job_index"], 0)

    def test_structure_from_text_missing_key_empty(self) -> None:
        import asyncio
        from unittest.mock import patch

        from server.resume_parse import structure_from_text

        with patch("server.resume_parse.LLM_API_KEY", ""):
            out = asyncio.run(structure_from_text("有字"))
            self.assertEqual(out["projects"], [])
            self.assertEqual(out["jobs"], [])

    def test_parse_resume_id_rejects_malformed(self) -> None:
        from uuid import UUID

        from server.resumes import parse_resume_id

        self.assertIsNone(parse_resume_id("not-a-uuid"))
        self.assertIsNone(parse_resume_id(""))
        parsed = parse_resume_id("3c405d22-0414-46b2-9400-8d9bb754723e")
        self.assertEqual(parsed, UUID("3c405d22-0414-46b2-9400-8d9bb754723e"))

    def test_confirm_requires_project_fields(self) -> None:
        from server.resumes import confirm_error

        self.assertIsNotNone(confirm_error({"projects": []}))
        self.assertIsNotNone(confirm_error({"projects": [{"name": "A", "responsibilities": "  "}]}))
        self.assertIsNone(confirm_error({"projects": [{"name": "收银台", "responsibilities": "写网关"}]}))

    def test_parse_job_does_not_skip_blank_profile(self) -> None:
        from server.resumes import should_skip_archive_replace

        self.assertFalse(should_skip_archive_replace(None, 0))
        self.assertTrue(should_skip_archive_replace("2026-01-01", 0))
        self.assertTrue(should_skip_archive_replace(None, 1))

    def test_public_resume_omits_raw_text(self) -> None:
        from server.resumes import public_resume

        payload = public_resume(
            {
                "id": "r1",
                "file_name": "a.pdf",
                "file_mime": "application/pdf",
                "file_size_bytes": 12,
                "parse_status": "parsed",
                "parse_error": None,
                "is_active": True,
                "created_at": None,
                "updated_at": None,
                "raw_text": "SECRET_RAW",
                "storage_key": "disk/path",
            },
            {"full_name": "张", "years_experience": 3, "current_title": None, "target_title": None, "summary": "", "skills": [], "confirmed_at": None},
            [],
            [],
        )
        dumped = str(payload)
        self.assertNotIn("SECRET_RAW", dumped)
        self.assertNotIn("raw_text", dumped)
        self.assertNotIn("disk/path", dumped)
        self.assertEqual(payload["fileName"], "a.pdf")
        self.assertEqual(payload["parseStatus"], "parsed")

    def test_interviewer_prompt_excludes_raw_and_invented(self) -> None:
        from server.resume_mock import (
            build_interviewer_messages,
            fallback_question,
            mentions_unknown_entity,
        )

        project = {
            "name": "收银台",
            "company": "爱连",
            "job_role": "后端",
            "tech_stack": ["Java"],
            "responsibilities": "鉴权与对账",
            "outcomes": "超时下降 30%",
        }
        messages = build_interviewer_messages(
            project,
            "transition",
            asked=["你在收银台负责哪一段？"],
            last_answer="我做鉴权",
            raw_text="SECRET_SHOULD_NOT_APPEAR",
        )
        blob = str(messages)
        self.assertNotIn("SECRET_SHOULD_NOT_APPEAR", blob)
        self.assertNotIn("raw_text", blob)
        self.assertIn("收银台", blob)
        self.assertIn("Java", blob)
        self.assertTrue(mentions_unknown_entity("请讲讲淘宝双十一大促架构", project))
        self.assertFalse(mentions_unknown_entity("收银台的鉴权你怎么验收？", project))
        self.assertFalse(mentions_unknown_entity("收银台超时、JWT 和 QPS 你怎么验收？", project))
        self.assertTrue(mentions_unknown_entity("你在字节做过收银台吗？", project))
        text = fallback_question(project)
        self.assertIn("收银台", text)
        self.assertIn("鉴权与对账", text)
        self.assertIn("负责哪一段", text)

    def test_generate_resume_question_fallback_and_retry(self) -> None:
        import asyncio
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from server.resume_mock import fallback_question, generate_resume_question

        project = {
            "name": "收银台",
            "company": "爱连",
            "job_role": "后端",
            "tech_stack": ["Java"],
            "responsibilities": "鉴权与对账",
            "outcomes": "超时下降 30%",
        }
        with patch("server.resume_mock.LLM_API_KEY", ""):
            missing = asyncio.run(generate_resume_question(project, "transition", [], ""))
        self.assertEqual(missing["question"], fallback_question(project))
        self.assertEqual(missing["gist"], "讲清本人职责和验收")
        self.assertEqual(missing["angle"], "ownership")
        self.assertEqual(missing["points"][0]["heading"], "职责")

        invented = (
            '{"question":"请讲讲淘宝双十一大促架构","gist":"x",'
            '"points":[{"heading":"a","body":"b"}],"angle":"metrics"}'
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"choices":[{"message":{"content":' + json.dumps(invented) + "}}]}"
        mock_resp.json.return_value = {"choices": [{"message": {"content": invented}}]}
        with patch("server.resume_mock.LLM_API_KEY", "k"), patch("server.resume_mock.async_http") as http_fn:
            http_fn.return_value.post = AsyncMock(return_value=mock_resp)
            invented_out = asyncio.run(generate_resume_question(project, "transition", [], "我做鉴权"))
        self.assertEqual(invented_out["question"], fallback_question(project))
        self.assertEqual(http_fn.return_value.post.call_count, 2)

        fail_resp = MagicMock()
        fail_resp.status_code = 502
        fail_resp.text = "<html>oops</html>"
        with patch("server.resume_mock.LLM_API_KEY", "k"), patch("server.resume_mock.async_http") as http_fn:
            http_fn.return_value.post = AsyncMock(return_value=fail_resp)
            http_out = asyncio.run(generate_resume_question(project, "experienced", ["已问"], "答"))
        self.assertEqual(http_out["question"], fallback_question(project))
        self.assertEqual(http_fn.return_value.post.call_count, 1)

        good = (
            '{"question":"收银台的鉴权你怎么验收？","gist":"讲验收",'
            '"points":[{"heading":"验收","body":"对账口径"}],"angle":"ownership"}'
        )
        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.text = '{"ok":true}'
        good_resp.json.return_value = {"choices": [{"message": {"content": good}}]}
        with patch("server.resume_mock.LLM_API_KEY", "k"), patch("server.resume_mock.async_http") as http_fn:
            http_fn.return_value.post = AsyncMock(return_value=good_resp)
            ok = asyncio.run(generate_resume_question(project, "transition", [], "我做鉴权"))
        self.assertEqual(ok["question"], "收银台的鉴权你怎么验收？")
        self.assertEqual(ok["angle"], "ownership")
        self.assertEqual(http_fn.return_value.post.call_count, 1)
        posted = http_fn.return_value.post.call_args
        self.assertNotIn("SECRET", str(posted))
        self.assertNotIn("raw_text", str(posted).lower())

    def test_mock_progress_two_projects_then_bank(self) -> None:
        from server.resume_mock import next_action, pick_second_project, plan_bank_parent_ids

        p1 = {"id": "p1", "sort_order": 0, "name": "A"}
        p2 = {"id": "p2", "sort_order": 1, "name": "B"}
        self.assertEqual(pick_second_project("p1", [p1, p2]), "p2")
        self.assertIsNone(pick_second_project("p1", [p1]))

        self.assertEqual(next_action([], ["p1", "p2"], []), ("resume", "p1", 1))
        six_resume = [{"project_id": "p1"}] * 3 + [{"project_id": "p2"}] * 3
        self.assertEqual(next_action(six_resume, ["p1", "p2"], []), ("bank", None, 1))
        self.assertEqual(next_action(six_resume, ["p1", "p2"], ["q1", "q2", "q3"]), ("done", None, 0))
        self.assertEqual(next_action([{"project_id": "p1"}] * 3, ["p1"], []), ("bank", None, 1))

        parents = [
            {"id": "a", "topicId": "t1"},
            {"id": "b", "topicId": "t2"},
            {"id": "c", "topicId": "t3"},
            {"id": "d", "topicId": "t1"},
        ]
        picked = plan_bank_parent_ids("session-seed", parents, 3)
        self.assertEqual(len(picked), 3)
        self.assertEqual(len({p["id"] for p in picked}), 3)
        topics = [row["topicId"] for row in picked]
        self.assertEqual(len(set(topics)), 3)
        self.assertEqual(plan_bank_parent_ids("session-seed", parents, 3), picked)

    def test_bank_etag_stable_and_matches_header(self) -> None:
        from server.bank import bank_etag, etag_matches

        first = {"release": {"version": "1.0.0"}, "questions": [{"id": "a"}]}
        second = {"release": {"version": "1.0.1"}, "questions": [{"id": "a"}]}
        tag = bank_etag(first)
        self.assertTrue(tag.startswith('"') and tag.endswith('"'))
        self.assertEqual(bank_etag(first), tag)
        self.assertNotEqual(bank_etag(second), tag)
        self.assertTrue(etag_matches(tag, tag))
        self.assertTrue(etag_matches(f"W/{tag}, \"other\"", tag))
        self.assertFalse(etag_matches('"nope"', tag))

    def test_upstream_helpers_are_async(self) -> None:
        import inspect

        from server.resume_parse import run_parse_pipeline, structure_from_text
        from server.transcribe import transcribe_audio
        from server.tts import synthesize_speech

        self.assertTrue(inspect.iscoroutinefunction(synthesize_speech))
        self.assertTrue(inspect.iscoroutinefunction(structure_from_text))
        self.assertTrue(inspect.iscoroutinefunction(run_parse_pipeline))
        self.assertTrue(inspect.iscoroutinefunction(transcribe_audio))


if __name__ == "__main__":
    unittest.main()
