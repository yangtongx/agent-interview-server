from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.config import PG_URI


@unittest.skipUnless(PG_URI, "需要 PG_URI")
class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from server.app import app

        cls.client = TestClient(app)

    def test_device_login_progress_and_home(self) -> None:
        device_id = str(uuid.uuid4())
        res = self.client.post(
            "/api/auth/device",
            json={"deviceId": device_id, "platform": "web", "audience": "transition"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        token = res.json()["token"]
        user = res.json()["user"]
        self.assertEqual(user["audience"], "transition")
        headers = {"Authorization": f"Bearer {token}"}

        bank = self.client.get("/api/bank")
        self.assertEqual(bank.status_code, 200, bank.text)
        parent_id = bank.json()["questions"][0]["id"]

        put = self.client.put("/api/progress", headers=headers, json={"mastered": [parent_id]})
        self.assertEqual(put.status_code, 200, put.text)
        self.assertIn(parent_id, put.json()["mastered"])

        home = self.client.get("/api/home", headers=headers)
        self.assertEqual(home.status_code, 200, home.text)
        payload = home.json()
        self.assertEqual(payload["audience"], "transition")
        self.assertTrue(payload["path"]["id"] in ("transition", "experienced"))
        self.assertGreater(payload["stats"]["onPath"], 0)
        if payload.get("next"):
            self.assertNotEqual(payload["next"]["questionId"], parent_id)

        me = self.client.patch("/api/me", headers=headers, json={"audience": "experienced"})
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["audience"], "experienced")

        unauth = self.client.get("/api/progress")
        self.assertEqual(unauth.status_code, 401)
        self.assertIn("error", unauth.json())

    def test_register_and_login(self) -> None:
        uid = "u" + uuid.uuid4().hex[:10]
        device_id = str(uuid.uuid4())
        res = self.client.post(
            "/api/auth/register",
            json={
                "provider": "local",
                "uid": uid,
                "password": "pass123",
                "displayName": "测测",
                "platform": "web",
                "deviceId": device_id,
                "audience": "experienced",
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        token = res.json()["token"]
        account_id = res.json()["user"]["id"]
        login = self.client.post(
            "/api/auth/login",
            json={
                "provider": "local",
                "uid": uid,
                "password": "pass123",
                "platform": "web",
                "deviceId": device_id,
            },
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertTrue(login.json()["token"])
        bad = self.client.post(
            "/api/auth/login",
            json={"provider": "local", "uid": uid, "password": "wrong1", "platform": "web"},
        )
        self.assertEqual(bad.status_code, 401)
        me = self.client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["displayName"], "测测")
        self.assertFalse(res.json()["user"].get("isGuest"))
        device = self.client.post(
            "/api/auth/device",
            json={"deviceId": device_id, "platform": "web"},
        )
        self.assertEqual(device.status_code, 200, device.text)
        self.assertNotEqual(device.json()["user"]["id"], account_id)
        self.assertTrue(device.json()["user"].get("isGuest"))
        device_me = self.client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {device.json()['token']}"},
        )
        self.assertEqual(device_me.status_code, 200)
        self.assertNotEqual(device_me.json()["id"], account_id)

    def test_guest_upgrade_does_not_keep_device_login(self) -> None:
        device_id = str(uuid.uuid4())
        guest = self.client.post(
            "/api/auth/device",
            json={"deviceId": device_id, "platform": "web", "audience": "transition"},
        )
        self.assertEqual(guest.status_code, 200, guest.text)
        guest_id = guest.json()["user"]["id"]
        uid = "u" + uuid.uuid4().hex[:10]
        registered = self.client.post(
            "/api/auth/register",
            json={
                "provider": "local",
                "uid": uid,
                "password": "pass123",
                "displayName": "正式",
                "platform": "web",
                "deviceId": device_id,
            },
        )
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertEqual(registered.json()["user"]["id"], guest_id)
        device = self.client.post(
            "/api/auth/device",
            json={"deviceId": device_id, "platform": "web"},
        )
        self.assertEqual(device.status_code, 200, device.text)
        self.assertNotEqual(device.json()["user"]["id"], guest_id)
        self.assertNotEqual(device.json()["user"]["displayName"], "正式")

    def test_ai_proxy_requires_auth_and_rejects_ssrf_base_url(self) -> None:
        payload = {
            "question": "为什么？",
            "gist": "讲清原因",
            "userAnswer": "因为缓存失效。",
            "apiKey": "sk-fake",
            "baseUrl": "http://127.0.0.1:9",
        }
        unauth = self.client.post("/api/judge", json=payload)
        self.assertEqual(unauth.status_code, 401, unauth.text)
        tts = self.client.post("/api/tts", json={"text": "你好", "apiKey": "sk-fake", "baseUrl": "http://127.0.0.1:9"})
        self.assertEqual(tts.status_code, 401, tts.text)
        asr = self.client.post(
            "/api/transcribe",
            data={"apiKey": "sk-fake", "baseUrl": "http://127.0.0.1:9"},
            files={"audio": ("a.wav", b"RIFF" + b"\x00" * 64, "audio/wav")},
        )
        self.assertEqual(asr.status_code, 401, asr.text)

        headers = self._auth()
        with patch("server.app.async_http") as mocked_http:
            judged = self.client.post("/api/judge", headers=headers, json=payload)
        self.assertEqual(judged.status_code, 400, judged.text)
        self.assertIn("error", judged.json())
        mocked_http.assert_not_called()
        forged = self.client.post(
            "/api/judge",
            headers={**headers, "X-Forwarded-For": "8.8.8.8"},
            json={"question": "为什么？", "gist": "讲清原因", "userAnswer": "短"},
        )
        self.assertIn(forged.status_code, (200, 400, 429))
        if forged.status_code != 429:
            self.assertNotIn("127.0.0.1", forged.text)

    def test_admin_requires_token(self) -> None:
        res = self.client.get("/api/admin/stats")
        self.assertIn(res.status_code, (401, 503))
        self.assertIn("error", res.json())

    def test_health_and_request_id(self) -> None:
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["db"])
        self.assertTrue(body["ok"])
        self.assertTrue(res.headers.get("x-request-id"))
        custom = self.client.get("/api/health", headers={"X-Request-Id": "trace-unit"})
        self.assertEqual(custom.headers.get("x-request-id"), "trace-unit")

    @patch.dict(os.environ, {}, clear=False)
    def test_admin_with_token(self) -> None:
        with patch("server.admin.ADMIN_TOKEN", "unit-admin-token"):
            res = self.client.get("/api/admin/stats", headers={"X-Admin-Token": "unit-admin-token"})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertIn("questions", res.json())
            page = self.client.get("/admin")
            self.assertEqual(page.status_code, 200)
            self.assertIn("题库中台", page.text)

    def _auth(self, platform: str = "harmony") -> dict[str, str]:
        device_id = str(uuid.uuid4())
        login = self.client.post("/api/auth/device", json={"deviceId": device_id, "platform": platform})
        self.assertEqual(login.status_code, 200, login.text)
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def test_resume_malformed_id_is_not_found(self) -> None:
        headers = self._auth()
        got = self.client.get("/api/resumes/not-a-uuid", headers=headers)
        self.assertEqual(got.status_code, 404, got.text)
        self.assertEqual(got.json()["error"], "简历不存在。")
        put = self.client.put(
            "/api/resumes/not-a-uuid",
            headers=headers,
            json={"profile": {}, "jobs": [], "projects": []},
        )
        self.assertEqual(put.status_code, 404, put.text)
        self.assertEqual(put.json()["error"], "简历不存在。")
        confirm = self.client.post("/api/resumes/not-a-uuid/confirm", headers=headers)
        self.assertEqual(confirm.status_code, 404, confirm.text)
        self.assertEqual(confirm.json()["error"], "简历不存在。")

    def test_resume_upload_requires_auth(self) -> None:
        res = self.client.post("/api/resumes", files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
        self.assertEqual(res.status_code, 401)
        self.assertIn("error", res.json())
        listed = self.client.get("/api/resumes")
        self.assertEqual(listed.status_code, 401)
        self.assertIn("error", listed.json())

    def test_resume_reject_doc_and_confirm_empty(self) -> None:
        from unittest.mock import patch

        device_id = str(uuid.uuid4())
        login = self.client.post("/api/auth/device", json={"deviceId": device_id, "platform": "harmony"})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        bad = self.client.post(
            "/api/resumes",
            headers=headers,
            files={"file": ("old.doc", b"xxx", "application/msword")},
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("docx", bad.json()["error"])

        with patch("server.app.run_parse_job"):
            up = self.client.post(
                "/api/resumes",
                headers=headers,
                files={"file": ("a.pdf", b"%PDF-1.4 hello", "application/pdf")},
            )
        self.assertEqual(up.status_code, 200, up.text)
        resume_id = up.json()["id"]
        self.assertIn(up.json()["parseStatus"], ("uploaded", "parsing", "failed", "needs_review", "parsed"))
        got = self.client.get(f"/api/resumes/{resume_id}", headers=headers)
        self.assertEqual(got.status_code, 200)
        self.assertNotIn("raw_text", got.json())
        self.assertNotIn("rawText", got.json())
        confirm = self.client.post(f"/api/resumes/{resume_id}/confirm", headers=headers)
        self.assertEqual(confirm.status_code, 400)

        put = self.client.put(
            f"/api/resumes/{resume_id}",
            headers=headers,
            json={
                "profile": {"fullName": "测", "yearsExperience": 5, "currentTitle": "Java", "targetTitle": "Agent", "summary": "", "skills": []},
                "jobs": [],
                "projects": [{"name": "改名项目", "responsibilities": "我做网关", "outcomes": "", "techStack": ["Java"], "jobId": None, "sortOrder": 0}],
            },
        )
        self.assertEqual(put.status_code, 200, put.text)
        ok = self.client.post(f"/api/resumes/{resume_id}/confirm", headers=headers)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertTrue(ok.json()["isActive"])
        self.assertTrue(ok.json()["confirmedAt"])
        listed = self.client.get("/api/resumes", headers=headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["activeId"], resume_id)

    def test_confirmed_parse_failure_keeps_archive(self) -> None:
        from server.resumes import run_parse_job

        headers = self._auth()
        with patch("server.app.run_parse_job"):
            up = self.client.post(
                "/api/resumes",
                headers=headers,
                files={"file": ("a.pdf", b"%PDF-1.4 hello", "application/pdf")},
            )
        self.assertEqual(up.status_code, 200, up.text)
        resume_id = up.json()["id"]
        put = self.client.put(
            f"/api/resumes/{resume_id}",
            headers=headers,
            json={
                "profile": {"fullName": "测", "skills": []},
                "jobs": [],
                "projects": [
                    {"name": "改名项目", "responsibilities": "我做网关", "outcomes": "", "techStack": ["Java"]},
                ],
            },
        )
        self.assertEqual(put.status_code, 200, put.text)
        ok = self.client.post(f"/api/resumes/{resume_id}/confirm", headers=headers)
        self.assertEqual(ok.status_code, 200, ok.text)
        before = self.client.get(f"/api/resumes/{resume_id}", headers=headers).json()
        with patch("server.resumes.run_parse_pipeline", side_effect=RuntimeError("boom")):
            run_parse_job(resume_id)
        after = self.client.get(f"/api/resumes/{resume_id}", headers=headers).json()
        self.assertEqual(after["parseStatus"], before["parseStatus"])
        self.assertTrue(after["confirmedAt"])
        self.assertEqual([item.get("name") for item in after.get("projects") or []], ["改名项目"])

    def test_parse_job_persists_llm_projects_on_blank_profile(self) -> None:
        from server.resumes import run_parse_job

        headers = self._auth()
        with patch("server.app.run_parse_job"):
            up = self.client.post(
                "/api/resumes",
                headers=headers,
                files={"file": ("a.pdf", b"%PDF-1.4 hello", "application/pdf")},
            )
        self.assertEqual(up.status_code, 200, up.text)
        resume_id = up.json()["id"]
        pipeline = {
            "raw_text": "x",
            "archive": {
                "profile": {},
                "jobs": [],
                "projects": [
                    {
                        "name": "收银台",
                        "responsibilities": "网关",
                        "outcomes": "",
                        "tech_stack": [],
                    }
                ],
            },
            "status": "parsed",
            "error": None,
        }
        with patch("server.resumes.run_parse_pipeline", return_value=pipeline):
            run_parse_job(resume_id)
        got = self.client.get(f"/api/resumes/{resume_id}", headers=headers)
        self.assertEqual(got.status_code, 200, got.text)
        names = [item.get("name") for item in got.json().get("projects") or []]
        self.assertIn("收银台", names)
        self.assertEqual(got.json()["parseStatus"], "parsed")

    def test_resume_mock_session_turn(self) -> None:
        from unittest.mock import patch

        device_id = str(uuid.uuid4())
        login = self.client.post(
            "/api/auth/device",
            json={"deviceId": device_id, "platform": "harmony", "audience": "transition"},
        )
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        with patch("server.app.run_parse_job"):
            up = self.client.post(
                "/api/resumes",
                headers=headers,
                files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            )
        rid = up.json()["id"]
        self.client.put(
            f"/api/resumes/{rid}",
            headers=headers,
            json={
                "profile": {"fullName": "测", "skills": []},
                "jobs": [],
                "projects": [
                    {"name": "改名项目", "responsibilities": "网关鉴权", "outcomes": "超时下降", "techStack": ["Java"]},
                    {"name": "第二项目", "responsibilities": "对账", "outcomes": "", "techStack": ["Redis"]},
                ],
            },
        )
        self.client.post(f"/api/resumes/{rid}/confirm", headers=headers)
        unconf = self.client.post("/api/mock/sessions", json={"kind": "resume", "resumeId": rid})
        self.assertEqual(unconf.status_code, 401)
        with patch("server.resume_mock.generate_resume_question", return_value={
            "question": "改名项目里你负责哪一段？",
            "gist": "职责",
            "points": [{"heading": "职责", "body": "讲清自己做的部分"}],
            "angle": "ownership",
        }):
            sess = self.client.post(
                "/api/mock/sessions",
                headers=headers,
                json={"kind": "resume", "resumeId": rid},
            )
            self.assertEqual(sess.status_code, 200, sess.text)
            sid = sess.json()["id"]
            t1 = self.client.post(f"/api/mock/sessions/{sid}/turn", headers=headers, json={})
        self.assertEqual(t1.status_code, 200, t1.text)
        self.assertEqual(t1.json()["itemKind"], "resume")
        self.assertIn("改名项目", t1.json()["question"]["question"])
        self.assertNotIn("SECRET", str(t1.json()))
        empty = self.client.post(f"/api/mock/sessions/{sid}/turn", headers=headers, json={})
        self.assertEqual(empty.status_code, 400)
        fin = self.client.post(f"/api/mock/sessions/{sid}/finish", headers=headers)
        self.assertEqual(fin.status_code, 200)
        self.assertEqual(fin.json()["status"], "finished")

    def test_resume_hidden_from_other_user(self) -> None:
        owner = self._auth()
        other = self._auth()
        with patch("server.app.run_parse_job"):
            up = self.client.post(
                "/api/resumes",
                headers=owner,
                files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            )
        self.assertEqual(up.status_code, 200, up.text)
        rid = up.json()["id"]
        hidden = self.client.get(f"/api/resumes/{rid}", headers=other)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.json()["error"], "简历不存在。")
        listed = self.client.get("/api/resumes", headers=other)
        self.assertEqual(listed.status_code, 200)
        ids = [item.get("id") for item in listed.json().get("items") or []]
        self.assertNotIn(rid, ids)

    def test_resume_mock_two_projects_then_bank(self) -> None:
        headers = self._auth()
        with patch("server.app.run_parse_job"):
            up = self.client.post(
                "/api/resumes",
                headers=headers,
                files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
            )
        rid = up.json()["id"]
        self.client.put(
            f"/api/resumes/{rid}",
            headers=headers,
            json={
                "profile": {"fullName": "测", "skills": []},
                "jobs": [],
                "projects": [
                    {"name": "改名项目", "responsibilities": "网关鉴权", "outcomes": "", "techStack": ["Java"]},
                    {"name": "第二项目", "responsibilities": "对账", "outcomes": "", "techStack": ["Redis"]},
                ],
            },
        )
        self.client.post(f"/api/resumes/{rid}/confirm", headers=headers)
        generated = {
            "question": "改名项目里你负责哪一段？",
            "gist": "职责",
            "points": [{"heading": "职责", "body": "讲清自己做的部分"}],
            "angle": "ownership",
        }
        with patch("server.resume_mock.generate_resume_question", return_value=generated):
            sess = self.client.post(
                "/api/mock/sessions",
                headers=headers,
                json={"kind": "resume", "resumeId": rid},
            )
            self.assertEqual(sess.status_code, 200, sess.text)
            sid = sess.json()["id"]
            kinds = []
            bank_ids = []
            turn = self.client.post(f"/api/mock/sessions/{sid}/turn", headers=headers, json={})
            self.assertEqual(turn.status_code, 200, turn.text)
            kinds.append(turn.json()["itemKind"])
            for _ in range(8):
                turn = self.client.post(
                    f"/api/mock/sessions/{sid}/turn",
                    headers=headers,
                    json={"answerText": "我做网关鉴权"},
                )
                self.assertEqual(turn.status_code, 200, turn.text)
                kind = turn.json()["itemKind"]
                kinds.append(kind)
                if kind == "bank":
                    bank_ids.append(turn.json().get("questionId"))
                if kind == "done":
                    break
        self.assertEqual(kinds[:6], ["resume"] * 6)
        self.assertGreater(len(kinds), 6)
        self.assertIn(kinds[6], ("bank", "done"))
        if kinds[6] == "bank":
            self.assertTrue(all(bank_ids))


if __name__ == "__main__":
    unittest.main()
