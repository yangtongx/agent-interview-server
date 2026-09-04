import type { AnswerBlock } from "./types";

const OPENERS = [
  "这道题我按 Java 转 Agent 的口径讲：先给能上线的结论，再对照我以前做后端时怎么处理同类问题。",
  "我先把整段答完。结论、做法、失败模式一起说，追问留到后面补。",
];

/**
 * 把要点收成一段可以开口 2–3 分钟的完整作答。
 * 面试时先讲这一段；分步和追问只是对照，不是第一遍就要背的提纲。
 */
export function composeFullAnswer(input: {
  gist: string;
  points: AnswerBlock[];
  bonus: string;
  redFlags: string;
}): string {
  const opener = OPENERS[0];
  const body = input.points
    .map((point) => point.body.trim())
    .filter(Boolean)
    .join("\n\n");

  return [
    opener,
    `先给一个能写进笔记的结论：${input.gist.trim()}`,
    body,
    `如果时间还够，我主动补一句：${input.bonus.trim()}`,
    `这些说法我不会往外蹦：${input.redFlags.trim()}`,
  ]
    .filter(Boolean)
    .join("\n\n");
}
