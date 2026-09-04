#!/usr/bin/env python3
"""Parse the extracted Ailian interview docx into data/ailian-bank.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

SRC = Path("/tmp/docx/extracted.txt")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "ailian-bank.json"

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
CIRCLED_HEAD = {
    "①": "第一点",
    "②": "第二点",
    "③": "第三点",
    "④": "第四点",
    "⑤": "第五点",
    "⑥": "第六点",
    "⑦": "第七点",
    "⑧": "第八点",
    "⑨": "第九点",
    "⑩": "第十点",
}

DIFFS = ["基础", "进阶", "场景", "深挖", "系统设计"]

CHAPTERS = [
    {
        "lo": 1,
        "hi": 10,
        "id": "ailian-langgraph",
        "index": 23,
        "name": "LangGraph 多 Agent 编排",
        "english": "LangGraph",
        "summary": "主图 StateGraph、7 个子 Agent、共享 State、checkpoint 与路由条件边。",
        "interviewerNote": "听现网口径：子 Agent 是 7 个不是 8 个，履约已并进权益。别把 LangGraph 答成框架偏好。",
        "angles": ["框架选型", "共享状态", "识别落点", "多意图队列", "子图嵌入", "路由函数", "Reducer", "退出机制", "人工中断", "可视化调试"],
        "redFlags": "把 LangGraph 说成个人偏好；坚称现网 8 个子 Agent；让每个子图再做一遍一级路由；checkpoint 只放进程内存。",
        "bonus": "主动讲清履约并进权益的产品理由，以及 RedisSaver 是为 Gunicorn 多 Worker 服务的。",
    },
    {
        "lo": 11,
        "hi": 20,
        "id": "ailian-intent",
        "index": 24,
        "name": "意图识别与智能路由",
        "english": "Intent Routing",
        "summary": "主图 intention_recognition、SDK 短路、LLM JSON 兜底、转人工与空意图。",
        "interviewerNote": "要能讲清为什么一级路由只允许发生在主图，以及历史按轮次截断不是按条数。",
        "angles": ["识别 Prompt", "上下文继承", "噪声过滤", "安全前置", "图片辅助", "转人工", "单多意图", "空意图兜底", "域边界", "评测"],
        "redFlags": "每个子 Agent 各自调 LLM 做一级分类；历史按「最近 10 条消息」截；空意图直接硬答医疗建议。",
        "bonus": "把 SDK 短路和 LLM 回退的分工讲清楚：清晰流量不付模型税。",
    },
    {
        "lo": 21,
        "hi": 31,
        "id": "ailian-rag",
        "index": 25,
        "name": "RAG 检索增强生成",
        "english": "RAG",
        "summary": "DashVector、权益知识库、召回阈值、幻觉控制与 Embedding 限流。",
        "interviewerNote": "权益条款问答走 RAG，查卡走 Java API。串了策略就是事故。",
        "angles": ["向量库", "阈值", "导入更新", "Embedding 限流", "注入格式", "其它域", "延迟", "幻觉", "text_type", "业务效果", "Collection 管理"],
        "redFlags": "所有问题都先检索；条款问答用模型背；没有阈值就调大 top_k；把纠错库和权益库混一个 Collection。",
        "bonus": "能讲 rerank 为什么比盲目加大 top_k 更适合条款问答。",
    },
    {
        "lo": 32,
        "hi": 42,
        "id": "ailian-multimodal",
        "index": 26,
        "name": "多模态图片理解",
        "english": "Multimodal",
        "summary": "药盒、报告、皮肤、商品图识别，结构化 JSON 写回共享 State。",
        "interviewerNote": "图片识别在意图之前，是为了给路由提供结构化信号，不是为了让模型看图聊天。",
        "angles": ["模型选型", "结构化输出", "拍药盒", "报告解读", "状态共享", "路由失败", "成本延迟", "皮肤识别", "URL 校验", "商品图辅助", "多图"],
        "redFlags": "用图片模型直接下诊断；多图不分流；识别失败还按原意图走写操作。",
        "bonus": "点出药盒/报告可跳过意图识别、直达队列，这是现网条件边而不是 prompt 里的 if。",
    },
    {
        "lo": 43,
        "hi": 53,
        "id": "ailian-prompt",
        "index": 27,
        "name": "Prompt 工程实践",
        "english": "Prompting",
        "summary": "主助手、意图、子 Agent 分层 Prompt，JSON 约束、注入与医疗边界。",
        "interviewerNote": "Prompt 是产品规格。边界、继承规则、禁止事项要写死，不要指望模型自觉。",
        "angles": ["Prompt 分层", "继承规则", "A/B 评估", "业务边界", "Few-shot", "JSON 约束", "注入防护", "流式影响", "主助手兜底", "投入占比", "医疗 Prompt"],
        "redFlags": "一个万能系统提示打天下；把评测集贴进 few-shot；用 Prompt 代替鉴权和安全闸门。",
        "bonus": "主动区分「模型绝对不能说」和「可以说但必须保守」。",
    },
    {
        "lo": 54,
        "hi": 64,
        "id": "ailian-tools",
        "index": 28,
        "name": "工具调用与槽位填充",
        "english": "Tools & Slots",
        "summary": "Intent → Slot → Tool 三段式、Pydantic 槽位、Java API 与改意图。",
        "interviewerNote": "槽位缺失要追问而不是猜。改主意必须弹栈，不能把旧工具结果当新意图。",
        "angles": ["三段式", "@tool", "Pydantic 槽位", "查卡对接", "改主意", "卡片格式化", "药盒二级意图", "幂等", "工具复用", "槽位歧义", "历史管理"],
        "redFlags": "槽位空了还调写工具；改意图不清队列；工具原始 JSON 一直留在意图历史里。",
        "bonus": "把 ChangeIntention 和多意图抢占讲成同一套调度，而不是两个故事。",
    },
    {
        "lo": 65,
        "hi": 74,
        "id": "ailian-sse",
        "index": 29,
        "name": "流式输出与 SSE",
        "english": "SSE",
        "summary": "graph_streamer、事件类型、PII 脱敏、思考流与失败事件。",
        "interviewerNote": "输出安全检测必须做在 SSE 流上。等图跑完再扫，用户已经看见违规字了。",
        "angles": ["SSE 协议", "graph_streamer", "PII 脱敏", "StreamingResponse", "思考流", "流内错误", "前端消费", "checkpoint 配合", "SSE 调试", "多轮配合"],
        "redFlags": "先把完整回复生成完再一次性吐；脱敏只做在落库；失败时连接直接断、前端转圈。",
        "bonus": "能画出事件类型和前端怎么渲染，比只说「我们用了 SSE」强。",
    },
    {
        "lo": 75,
        "hi": 85,
        "id": "ailian-engineering",
        "index": 30,
        "name": "工程化与生产部署",
        "english": "Production",
        "summary": "Gunicorn 多 Worker、RedisSaver、Nacos、JWT、Request-ID。",
        "interviewerNote": "多 Worker 丢会话是必考。MemorySaver 过不了这关。",
        "angles": ["多 Worker", "双 Key", "Lua 原子写", "Nacos", "JWT 白名单", "Request-ID", "镜像优化", "Gunicorn 参数", "Dynaconf", "日志", "CI/CD"],
        "redFlags": "会话放进程 dict；密钥明文进 Nacos 还当最佳实践；没有 Request-ID 还说能排障。",
        "bonus": "主动承认密钥明文是要还的债，比装完美更像架构师。",
    },
    {
        "lo": 86,
        "hi": 95,
        "id": "ailian-compliance",
        "index": 31,
        "name": "医疗业务与安全合规",
        "english": "Healthcare Safety",
        "summary": "心理危机、处方流转、慢病边界、等保与「宁可少说」。",
        "interviewerNote": "医疗场景失败模式是害人，不是答得不够聪明。危机检测必须失败关闭。",
        "angles": ["合规红线", "心理危机", "处方流转", "产品名", "慢病问诊", "加密脱敏", "等保", "容错", "人机协同", "数据生命周期"],
        "redFlags": "危机走失败放行；AI 直接开处方或指导用药剂量；把皮肤识别说成诊断。",
        "bonus": "把危机检测、输入审核、输出审核讲成三套机制，不要揉成「我们有安全」。",
    },
    {
        "lo": 96,
        "hi": 105,
        "id": "ailian-java",
        "index": 32,
        "name": "Java 后端协同",
        "english": "Java Collaboration",
        "summary": "OAuth2/JWT 复用、HTTP 主数据、TCC 边界、监控与转岗思考。",
        "interviewerNote": "AI 服务是接入方不是鉴权中心。一致性靠业务 API 和幂等，不靠模型记忆。",
        "angles": ["HTTP 通信", "OAuth2", "TCC", "Spring Cloud", "鉴权复用", "MQ", "Redis 分工", "监控告警", "架构改进", "转岗挑战"],
        "redFlags": "Python 自己再做一套登录；用 LLM 保证分布式事务；把 TCC 和对话状态混为一谈。",
        "bonus": "能讲清哪些一致性必须回 Java，哪些只要 Redis checkpoint 就够。",
    },
    {
        "lo": 106,
        "hi": 113,
        "id": "ailian-typo",
        "index": 33,
        "name": "输入纠错与 SDK 化",
        "english": "Typo SDK",
        "summary": "独立纠错 SDK、hybrid 两阶段、专有名词保护、写回 State。",
        "interviewerNote": "纠错做成 SDK 是为了产品名不被闲聊模型改坏。这是现网关键决策。",
        "angles": ["节点位置", "为何 SDK", "hybrid 顺序", "专有名词", "写回 State", "独立 HTTP", "库隔离", "SLA"],
        "redFlags": "让闲聊模型顺手改产品名；短词「卡」被替换成某张具体卡；纠错库和权益 RAG 共用 Collection。",
        "bonus": "把 hybrid 的 business → language 顺序讲明白，以及误伤比漏改更贵。",
    },
    {
        "lo": 114,
        "hi": 119,
        "id": "ailian-intent-sdk",
        "index": 34,
        "name": "意图 SDK 与配置化目录",
        "english": "Intent SDK",
        "summary": "检索短路、YAML 目录、混合检索、bailian_exec_plan 与履约合并。",
        "interviewerNote": "加意图应该改 YAML，而不是改七个子图。履约并进权益要能讲产品理由。",
        "angles": ["短路回退", "YAML 目录", "混合检索", "exec_plan", "启发式回退", "履约合并"],
        "redFlags": "SDK 和 LLM 同时出结果还靠 prompt 和解；启发式关键词和 SDK 抢决策；加意图就要改主图代码。",
        "bonus": "点出 bailian_exec_plan 为什么 LLM 路径要显式清掉。",
    },
    {
        "lo": 120,
        "hi": 124,
        "id": "ailian-safety-gates",
        "index": 35,
        "name": "内容安全双通道",
        "english": "Safety Gates",
        "summary": "进图前审输入、SSE 上审输出，与图内心理危机检测分开。",
        "interviewerNote": "三套机制职责不同。失败语义也不同：危机失败关闭，护栏失败开放要讲清楚。",
        "angles": ["三套机制", "流上审核", "注入 vs 安全", "不能说 vs 保守说", "误拦处置"],
        "redFlags": "把内容安全和 Prompt Injection 当成一回事；输出审核等整图结束；护栏误拦了还先改 Prompt。",
        "bonus": "能讲失败关闭和失败开放分别用在哪条闸门。",
    },
    {
        "lo": 125,
        "hi": 129,
        "id": "ailian-benefit",
        "index": 36,
        "name": "权益执行计划与话术模版",
        "english": "Benefit Plan",
        "summary": "少调 LLM、模版填空、RAG 精排、查卡/条款/申请三条策略不串。",
        "interviewerNote": "权益能模版填空就不要让模型自由发挥。这是合约话术，不是创意写作。",
        "angles": ["降 LLM 次数", "模版优先", "召回精排", "三策略隔离", "灰度"],
        "redFlags": "查卡也走 RAG；条款问答让模型发挥；申请服务不弹卡片直接承诺时效。",
        "bonus": "按调用链讲清如何把每请求 5 次 LLM 压到 1–2 次。",
    },
    {
        "lo": 130,
        "hi": 134,
        "id": "ailian-platform",
        "index": 37,
        "name": "能力外置、热更新与可观测",
        "english": "Platform Ops",
        "summary": "独立 HTTP 能力、飞书日志 Hook、LLM 代理热更新、评测闭环。",
        "interviewerNote": "没有评测的 Agent 只是能 demo。架构师要定闸门，不是只定框架。",
        "angles": ["能力外置", "飞书告警", "LLM 代理", "历史窗口", "评测闭环"],
        "redFlags": "把一年聊天塞进 context；Nacos 改模型配置还要发版；没有黄金集就上线。",
        "bonus": "把窗口、检索、主数据三种记忆讲成三句话，不要只说「我们有记忆」。",
    },
]


def chapter_for(n: int) -> dict:
    for ch in CHAPTERS:
        if ch["lo"] <= n <= ch["hi"]:
            return ch
    raise KeyError(n)


def first_sentence(text: str, max_len: int = 160) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"[^。！？]+[。！？]?", t)
    s = (m.group(0) if m else t).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def clip_title(text: str, max_len: int = 36) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    del max_len
    return re.sub(r"^你们(?:的)?\s*", "", t)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？])", text)
    return [p.strip() for p in parts if p.strip()]


def split_points(text: str) -> list[dict]:
    t = text.strip()
    if any(ch in t for ch in CIRCLED[:8]):
        chunks = re.split(r"(?=[①②③④⑤⑥⑦⑧⑨⑩])", t)
        points: list[dict] = []
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk[0] in CIRCLED_HEAD:
                heading = CIRCLED_HEAD[chunk[0]]
                body = chunk[1:].lstrip("、.．:： ").strip()
                if not body:
                    body = chunk
            else:
                heading = "先给判断"
                body = chunk
            if body:
                points.append({"heading": heading, "body": body})
        if len(points) >= 2:
            return points[:8]

    paras = [p.strip() for p in re.split(r"\n+", t) if p.strip()]
    if len(paras) >= 2:
        labels = ["先讲清楚", "接着展开", "再落到实现", "最后收束", "补一句约束"]
        return [
            {"heading": labels[i] if i < len(labels) else f"第{i+1}层", "body": p}
            for i, p in enumerate(paras[:5])
        ]

    sents = split_sentences(t)
    if len(sents) <= 2:
        return [{"heading": "完整说明", "body": t}]

    groups: list[str] = []
    buf = ""
    for s in sents:
        if not buf:
            buf = s
        elif len(buf) < 90:
            buf += s
        else:
            groups.append(buf)
            buf = s
    if buf:
        groups.append(buf)
    if len(groups) == 1:
        return [{"heading": "完整说明", "body": groups[0]}]
    labels = ["结论怎么落到现网", "关键机制", "失败与兜底", "工程取舍"]
    return [
        {"heading": labels[i] if i < len(labels) else f"补充 {i+1}", "body": g}
        for i, g in enumerate(groups[:4])
    ]


def parse_followups(block: str) -> list[dict]:
    block = block.strip()
    if not block:
        return []
    parts = re.split(r"追问(\d+)\s*[.．、]\s*", block)
    # ['preamble', '1', 'body', '2', 'body', ...]
    out: list[dict] = []
    i = 1
    while i + 1 < len(parts):
        body = parts[i + 1].strip()
        if "答：" in body:
            q, a = body.split("答：", 1)
        elif "答:" in body:
            q, a = body.split("答:", 1)
        else:
            q, a = body, ""
        q = re.sub(r"\s+", " ", q).strip()
        a = a.strip()
        a = re.sub(r"\n+", "\n", a).strip()
        if not q:
            i += 2
            continue
        points = split_points(a) if a else [{"heading": "先开口", "body": "先用现网事实答，不要先背框架名。"}]
        gist = first_sentence(a) if a else q
        out.append(
            {
                "title": clip_title(q, 32),
                "question": q,
                "fullAnswer": a,
                "gist": gist,
                "points": points,
                "bonus": "把现网字段名、失败语义和为什么这样选讲出来。",
                "redFlags": "只复述父题结论；给不出字段或失败路径。",
            }
        )
        i += 2
    return out


def parse() -> dict:
    text = SRC.read_text(encoding="utf-8")
    intro_m = re.search(
        r"开场：一分钟自我介绍\n(.*?)\n目录概览",
        text,
        flags=re.S,
    )
    intro = intro_m.group(1).strip() if intro_m else ""

    chunks = re.split(r"^Q(\d+)\.\s*", text, flags=re.M)
    questions: list[dict] = []
    i = 1
    while i + 1 < len(chunks):
        n = int(chunks[i])
        body = chunks[i + 1]
        # stop before next leftover footer
        body = re.split(r"^—— 本题库", body, maxsplit=1, flags=re.M)[0]
        if "【参考答案】" not in body:
            raise RuntimeError(f"Q{n} missing 参考答案")
        q_part, rest = body.split("【参考答案】", 1)
        question = re.sub(r"\s+", " ", q_part.strip()).strip()
        if "【拓展追问】" in rest:
            answer, follow_block = rest.split("【拓展追问】", 1)
        else:
            answer, follow_block = rest, ""
        answer = answer.strip()
        followups = parse_followups(follow_block)
        ch = chapter_for(n)
        idx_in_ch = n - ch["lo"]
        angles = ch["angles"]
        questions.append(
            {
                "id": f"A{n}",
                "srcNo": n,
                "topicId": ch["id"],
                "difficulty": DIFFS[idx_in_ch % len(DIFFS)],
                "angle": angles[idx_in_ch] if idx_in_ch < len(angles) else angles[idx_in_ch % len(angles)],
                "title": clip_title(question),
                "question": question,
                "fullAnswer": answer,
                "gist": first_sentence(answer),
                "points": split_points(answer),
                "bonus": ch["bonus"],
                "redFlags": ch["redFlags"],
                "followups": followups,
            }
        )
        i += 2

    topics = [
        {
            "id": ch["id"],
            "index": ch["index"],
            "bank": "ailian",
            "name": ch["name"],
            "english": ch["english"],
            "summary": ch["summary"],
            "interviewerNote": ch["interviewerNote"],
        }
        for ch in CHAPTERS
    ]

    expected = [ch["hi"] - ch["lo"] + 1 for ch in CHAPTERS]
    counts: dict[str, int] = {}
    for q in questions:
        counts[q["topicId"]] = counts.get(q["topicId"], 0) + 1
    for ch, exp in zip(CHAPTERS, expected):
        got = counts.get(ch["id"], 0)
        if got != exp:
            raise SystemExit(f"{ch['id']} expected {exp} questions, got {got}")

    if len(questions) != 134:
        raise SystemExit(f"expected 134 questions, got {len(questions)}")

    return {
        "intro": {
            "candidate": "杨会东",
            "title": "一分钟自我介绍",
            "body": intro,
        },
        "topics": topics,
        "questions": questions,
    }


def main() -> None:
    data = parse()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    follow_n = sum(len(q["followups"]) for q in data["questions"])
    empty = [q["id"] for q in data["questions"] if not q["followups"]]
    short_ans = [q["id"] for q in data["questions"] if len(q["fullAnswer"]) < 40]
    print(f"wrote {OUT}")
    print(f"parents={len(data['questions'])} followups={follow_n}")
    print(f"empty_followups={empty}")
    print(f"short_answers={short_ans}")
    print("per_topic", {t["id"]: sum(1 for q in data["questions"] if q["topicId"] == t["id"]) for t in data["topics"]})


if __name__ == "__main__":
    main()
