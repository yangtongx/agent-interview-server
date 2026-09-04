import type { Question } from "@/data/types";

export function formatQuestionCode(id: string): string {
  const [head, ...rest] = id.split(".");
  if (head.startsWith("A")) {
    const n = head.slice(1);
    return rest.length ? [n, ...rest].join(".") : n;
  }
  const padded = head.padStart(2, "0");
  return rest.length ? [padded, ...rest].join(".") : padded;
}

export function displayQuestionCode(id: string): string {
  const [head, ...rest] = id.split(".");
  if (head.startsWith("A")) {
    const n = head.slice(1);
    const tail = rest.length ? `.${rest.join(".")}` : "";
    return `爱连 ${n}${tail}`;
  }
  return `Q${formatQuestionCode(id)}`;
}

export function isAilianQuestion(id: string): boolean {
  return id.startsWith("A");
}

export function flattenQuestions(nodes: Question[]): Question[] {
  const out: Question[] = [];
  const walk = (q: Question) => {
    out.push(q);
    for (const child of q.children) walk(child);
  };
  for (const node of nodes) walk(node);
  return out;
}

export function countQuestions(nodes: Question[]): number {
  return flattenQuestions(nodes).length;
}

export function questionHaystack(q: Question): string {
  return [
    q.id,
    q.title,
    q.question,
    q.gist,
    q.fullAnswer,
    q.angle,
    q.bonus,
    q.redFlags,
    q.source?.label ?? "",
    ...q.points.map((p) => `${p.heading} ${p.body}`),
    ...q.children.map(questionHaystack),
  ].join("\n");
}

export function treeMatches(
  q: Question,
  opts: {
    query: string;
    onlyUnmastered?: boolean;
    mastered: string[];
  },
): boolean {
  const nodeHit = nodeMatches(q, opts);
  if (nodeHit) return true;
  return q.children.some((child) => treeMatches(child, opts));
}

export function nodeMatches(
  q: Question,
  opts: {
    query: string;
    onlyUnmastered?: boolean;
    mastered: string[];
  },
): boolean {
  if (opts.onlyUnmastered && opts.mastered.includes(q.id)) {
    return false;
  }
  if (!opts.query) return true;
  const hay = [
    q.id,
    q.title,
    q.question,
    q.gist,
    q.fullAnswer,
    q.angle,
    q.bonus,
    q.redFlags,
    q.source?.label ?? "",
    ...q.points.map((p) => `${p.heading} ${p.body}`),
  ]
    .join("\n")
    .toLowerCase();
  return hay.includes(opts.query);
}

export function descendantMatchesQuery(q: Question, query: string): boolean {
  if (!query) return false;
  return q.children.some((child) => {
    const hay = [
      child.title,
      child.question,
      child.gist,
      child.fullAnswer,
      child.angle,
      ...child.points.map((p) => `${p.heading} ${p.body}`),
    ]
      .join("\n")
      .toLowerCase();
    return hay.includes(query) || descendantMatchesQuery(child, query);
  });
}
