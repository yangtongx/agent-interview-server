from __future__ import annotations

import json
import re
from typing import Any

CJK_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_\-]{2,}")


def looks_like_html(text: str) -> bool:
    head = text.strip()[:32].lower()
    return head.startswith("<!doctype") or head.startswith("<html") or head.startswith("<?xml")


def parse_json_text(text: str, label: str) -> Any:
    trimmed = text.strip()
    if not trimmed:
        raise ValueError(f"{label}是空的")
    if looks_like_html(trimmed) or trimmed.startswith("<"):
        raise ValueError(
            f"{label}返回了网页而不是 JSON。若你填的是通义，接口地址必须是 "
            "https://dashscope.aliyuncs.com/compatible-mode/v1 。"
        )
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}不是合法 JSON") from exc


def extract_json_object(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    raw = (fenced.group(1) if fenced else text).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型没有返回 JSON 对象，请换一个模型或重试。")
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError("模型输出无法解析成 JSON，请重试一次。") from exc


def completions_url(base_url: str) -> str:
    trimmed = base_url.strip().rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


def looks_like_speech_model(model: str) -> bool:
    return bool(re.search(r"(?:^|[-_/])(?:tts|asr|whisper)(?:$|[-_/])", model, re.I) or re.search(r"speech", model, re.I))


def chat_model_for(base_url: str, model: str, provider: str = "") -> str:
    if not looks_like_speech_model(model):
        return model
    if provider == "qwen" or re.search(r"dashscope", base_url, re.I):
        return "qwen-plus"
    if provider == "openai":
        return "gpt-4o-mini"
    return model


def _as_string_array(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        if len(out) >= 6:
            break
    return out[:6]


def normalize_judge_result(raw: Any, mode: str, model: str | None = None) -> dict[str, Any]:
    obj = raw if isinstance(raw, dict) else {}
    try:
        score = int(round(float(obj.get("score", 5))))
    except (TypeError, ValueError):
        score = 5
    score = min(10, max(1, score))
    verdict = obj.get("verdict")
    if verdict not in ("过", "追问", "挂"):
        verdict = "过" if score >= 8 else "追问" if score >= 5 else "挂"
    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        summary = "面试官听完了，但总评解析失败。"
    return {
        "mode": mode,
        "model": model,
        "score": score,
        "verdict": verdict,
        "summary": summary.strip(),
        "strengths": _as_string_array(obj.get("strengths")),
        "gaps": _as_string_array(obj.get("gaps")),
        "redFlagsHit": _as_string_array(obj.get("redFlagsHit")),
        "nextAsk": obj.get("nextAsk", "").strip() if isinstance(obj.get("nextAsk"), str) else "",
        "betterOpening": obj.get("betterOpening", "").strip() if isinstance(obj.get("betterOpening"), str) else "",
    }


def _tokens(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).lower() for m in CJK_TOKEN.finditer(text or "")))


def mock_judge(user_answer: str, gist: str, points: list[dict], red_flags: str, bonus: str) -> dict[str, Any]:
    answer = user_answer.strip()
    if len(answer) < 24:
        return {
            "mode": "mock",
            "score": 2,
            "verdict": "挂",
            "summary": "这不像完整作答。面试官还没听完结论，就会打断你重来。",
            "strengths": [],
            "gaps": ["先用两三句话把结论说完，再展开机制和失败模式。"],
            "redFlagsHit": [],
            "nextAsk": "你的结论是什么？一句话。",
            "betterOpening": gist,
        }
    corpus = "\n".join(
        [
            gist,
            *[
                f"{p.get('heading', '')} {p.get('body', '')}"
                for p in points
                if isinstance(p, dict)
            ],
            bonus,
        ]
    )
    need = [t for t in _tokens(corpus) if len(t) >= 2][:28]
    got = set(_tokens(answer))
    hit = [t for t in need if t in got]
    coverage = (len(hit) / len(need)) if need else 0
    flag_parts = [s.strip() for s in re.split(r"[；;。]", red_flags) if len(s.strip()) >= 4]
    red_flags_hit = []
    for flag in flag_parts:
        keys = _tokens(flag)[:3]
        if keys and all(k in got or k in answer for k in keys):
            red_flags_hit.append(flag)
    score = round(3 + coverage * 6)
    if len(answer) > 180:
        score += 1
    if red_flags_hit:
        score = min(score, 4)
    score = min(10, max(1, score))
    verdict = "挂" if red_flags_hit or score < 5 else "过" if score >= 8 else "追问"
    missing = [
        f"没讲到「{p.get('heading', '')}」"
        for p in points
        if isinstance(p, dict) and not any(t in got for t in _tokens(p.get("body", ""))[:4])
    ][:3]
    first = next((p for p in points if isinstance(p, dict)), None)
    return {
        "mode": "mock",
        "score": score,
        "verdict": verdict,
        "summary": "离线规则看起来覆盖了要点。接上大模型后，面试官会按现网口径再挖一层。"
        if verdict == "过"
        else "这是没接大模型时的规则点评：看覆盖率和禁区，不看论证质量。填入 API Key 后会换成真面试官。",
        "strengths": [f"提到了「{t}」" for t in hit[:4]],
        "gaps": missing or ["把失败模式和兜底讲具体，不要停在概念层。"],
        "redFlagsHit": red_flags_hit,
        "nextAsk": f"刚才「{first.get('heading')}」在现网里对应哪个字段或节点？" if first else "给一个会翻车的反例。",
        "betterOpening": gist,
    }


def build_judge_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    points = payload.get("points") or []
    reference_parts = [
        f"结论：{payload.get('gist', '')}",
        *[
            f"{i}. {p.get('heading', '')}：{p.get('body', '')}"
            for i, p in enumerate((x for x in points if isinstance(x, dict)), 1)
        ],
        f"加分：{payload.get('bonus', '')}",
        f"答砸信号：{payload.get('redFlags', '')}",
    ]
    full = (payload.get("fullAnswer") or "")[:2400]
    if full:
        reference_parts.append(f"完整作答参考：\n{full}")
    reference = "\n".join(reference_parts)
    bank = "爱连现网" if payload.get("bank") == "ailian" else "通用口径"
    system = """你是阿里巴巴的 AI Agent 面试官。根据参考口径点评候选人的口述答案。
用中文。像现场面试：直接、具体、不鸡汤。
不要把参考答案整段复述给候选人。可以点名他漏了哪个机制、字段、失败语义。
爱连健康现网题：子 Agent 是 7 个不是 8 个，履约已并进权益；不要鼓励演示稿口径。

只返回一个 JSON 对象，不要 markdown：
{
  "score": 1到10的整数,
  "verdict": "过" 或 "追问" 或 "挂",
  "summary": "两句总评",
  "strengths": ["优点，最多4条"],
  "gaps": ["缺口，最多4条"],
  "redFlagsHit": ["他踩到的雷，没有则空数组"],
  "nextAsk": "你下一句追问",
  "betterOpening": "替他改写的开头2到4句，口语，不要小标题"
}

评分：结论先行且有取舍、能讲失败/兜底，才给7分以上；只背框架名最高5分；命中答砸信号倾向「挂」。"""
    user = f"""题目（{bank} · {payload.get("topicName", "")}）：
{payload.get("question", "")}

参考口径：
{reference}

候选人刚才的口述：
{(payload.get("userAnswer") or "")[:4000]}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
