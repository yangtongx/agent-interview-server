import { existsSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { expandParents } from "@/data/expand";
import type { Difficulty, ParentDraft, Question, Topic } from "@/data/types";
import type { AilianIntro, BankStats, CandidateProfile, ClientBank } from "@/lib/bank-types";
import { flattenQuestions } from "@/lib/question-tree";

export type { AilianIntro, BankStats, ClientBank } from "@/lib/bank-types";

export type BankPayload = {
  topics: Topic[];
  questions: Question[];
  ailianIntro: AilianIntro;
  candidate: CandidateProfile | null;
  stats: BankStats & { dataDir: string };
};

type RawPoint = { heading: string; body: string };

type RawFollowup = {
  title: string;
  question: string;
  fullAnswer: string;
  gist: string;
  points: RawPoint[];
  bonus: string;
  redFlags: string;
};

type RawAilianParent = RawFollowup & {
  id: string;
  srcNo?: number;
  topicId: string;
  difficulty: Difficulty;
  angle: string;
  followups: RawFollowup[];
};

type AilianFile = {
  intro: AilianIntro;
  topics: Topic[];
  questions: RawAilianParent[];
};

const BANK_FILES = ["alibaba-topics.json", "alibaba-parents.json", "ailian-bank.json"] as const;
const OPTIONAL_FILES = ["candidate.json"] as const;

type CacheEntry = { key: string; bank: BankPayload };

let cache: CacheEntry | null = null;

function bankPath(dir: string, name: string) {
  return path.join(/* turbopackIgnore: true */ dir, name);
}

function dirHasBank(dir: string) {
  return BANK_FILES.every((name) => existsSync(/* turbopackIgnore: true */ bankPath(dir, name)));
}

export function resolveQuestionDataDir(): string {
  const env = process.env.QUESTION_DATA_DIR?.trim();
  const candidates = [
    env,
    "/data",
    path.join(process.cwd(), "data"),
    path.resolve(process.cwd(), "..", "data"),
  ].filter((dir): dir is string => Boolean(dir));

  for (const dir of candidates) {
    if (dirHasBank(dir)) return dir;
  }

  throw new Error(
    "题库 JSON 找不到。请把 alibaba-topics.json、alibaba-parents.json、ailian-bank.json 放到服务器 QUESTION_DATA_DIR（默认 /data）或项目 data/ 目录。",
  );
}

function readJson<T>(file: string): T {
  const text = readFileSync(/* turbopackIgnore: true */ file, "utf8");
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`${path.basename(file)} 不是合法 JSON。`);
  }
}

function cacheKey(dir: string) {
  return [...BANK_FILES, ...OPTIONAL_FILES].map((name) => {
    const file = bankPath(dir, name);
    try {
      return `${name}:${statSync(/* turbopackIgnore: true */ file).mtimeMs}`;
    } catch {
      return `${name}:missing`;
    }
  }).join("|");
}

function toChild(
  parentId: string,
  topicId: string,
  difficulty: Difficulty,
  followup: RawFollowup,
  index: number,
): Question {
  return {
    id: `${parentId}.${index + 1}`,
    parentId,
    level: 2,
    topicId,
    difficulty,
    angle: `追问 ${index + 1}`,
    title: followup.title,
    question: followup.question,
    gist: followup.gist,
    points: followup.points,
    bonus: followup.bonus,
    redFlags: followup.redFlags,
    fullAnswer: followup.fullAnswer,
    children: [],
  };
}

function toAilianParent(rawQ: RawAilianParent): Question {
  return {
    id: rawQ.id,
    level: 1,
    topicId: rawQ.topicId,
    difficulty: rawQ.difficulty,
    angle: rawQ.angle,
    title: rawQ.title,
    question: rawQ.question,
    gist: rawQ.gist,
    points: rawQ.points,
    bonus: rawQ.bonus,
    redFlags: rawQ.redFlags,
    fullAnswer: rawQ.fullAnswer,
    children: rawQ.followups.map((followup, index) =>
      toChild(rawQ.id, rawQ.topicId, rawQ.difficulty, followup, index),
    ),
  };
}

export function readBankFiles(dir = resolveQuestionDataDir()) {
  const alibabaTopics = readJson<Topic[]>(bankPath(dir, "alibaba-topics.json"));
  const alibabaParents = readJson<ParentDraft[]>(bankPath(dir, "alibaba-parents.json"));
  const ailian = readJson<AilianFile>(bankPath(dir, "ailian-bank.json"));
  if (!Array.isArray(alibabaTopics) || alibabaTopics.length === 0) {
    throw new Error("alibaba-topics.json 里没有主题。");
  }
  if (!Array.isArray(alibabaParents) || alibabaParents.length === 0) {
    throw new Error("alibaba-parents.json 里没有题目。");
  }
  if (!Array.isArray(ailian.topics) || !Array.isArray(ailian.questions) || !ailian.intro) {
    throw new Error("ailian-bank.json 结构不对，需要 intro、topics、questions。");
  }
  const candidateFile = bankPath(dir, "candidate.json");
  let candidate: CandidateProfile | null = null;
  if (existsSync(/* turbopackIgnore: true */ candidateFile)) {
    candidate = readJson<CandidateProfile>(candidateFile);
  }
  return { dir, alibabaTopics, alibabaParents, ailian, candidate };
}

export function buildBank(files = readBankFiles()): BankPayload {
  const alibabaQuestions = expandParents(files.alibabaParents, files.alibabaTopics);
  const ailianQuestions = files.ailian.questions.map(toAilianParent);
  const topics: Topic[] = [...files.alibabaTopics, ...files.ailian.topics];
  const questions: Question[] = [...alibabaQuestions, ...ailianQuestions];
  const allNodes = flattenQuestions(questions);
  return {
    topics,
    questions,
    ailianIntro: files.ailian.intro,
    candidate: files.candidate,
    stats: {
      totalParents: questions.length,
      totalQuestions: allNodes.length,
      alibabaParents: alibabaQuestions.length,
      ailianParents: ailianQuestions.length,
      alibabaTopics: files.alibabaTopics.length,
      ailianTopics: files.ailian.topics.length,
      dataDir: files.dir,
    },
  };
}

export function loadBank(force = false): BankPayload {
  const dir = resolveQuestionDataDir();
  const key = cacheKey(dir);
  if (!force && cache && cache.key === key) return cache.bank;
  const bank = buildBank(readBankFiles(dir));
  cache = { key, bank };
  return bank;
}

export function publicBank(bank = loadBank()): ClientBank {
  return {
    topics: bank.topics,
    questions: bank.questions,
    ailianIntro: bank.ailianIntro,
    candidate: bank.candidate,
    stats: {
      totalParents: bank.stats.totalParents,
      totalQuestions: bank.stats.totalQuestions,
      alibabaParents: bank.stats.alibabaParents,
      ailianParents: bank.stats.ailianParents,
      alibabaTopics: bank.stats.alibabaTopics,
      ailianTopics: bank.stats.ailianTopics,
      fromFiles: true,
    },
  };
}
