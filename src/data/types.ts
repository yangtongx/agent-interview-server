export type Difficulty = "基础" | "进阶" | "场景" | "深挖" | "系统设计";

export type QuestionBank = "alibaba" | "ailian";

export type Topic = {
  id: string;
  index: number;
  bank: QuestionBank;
  name: string;
  english: string;
  summary: string;
  interviewerNote: string;
};

export type AnswerBlock = {
  heading: string;
  body: string;
};

export type QuestionSource = {
  label: string;
  url: string;
};

export type QuestionLevel = 1 | 2 | 3;

export type Question = {
  id: string;
  parentId?: string;
  /** 仅当本题必须沿用另一道追问的结论时填写。 */
  prerequisiteId?: string;
  level: QuestionLevel;
  topicId: string;
  difficulty: Difficulty;
  angle: string;
  title: string;
  question: string;
  gist: string;
  points: AnswerBlock[];
  bonus: string;
  redFlags: string;
  /** 公开面经题保留出处；不代表招聘方官方题库。 */
  source?: QuestionSource;
  /** 完整作答：先把 2–3 分钟讲完，再看分步和追问。 */
  fullAnswer: string;
  children: Question[];
};

export type ParentDraft = Omit<
  Question,
  "id" | "level" | "parentId" | "children" | "fullAnswer"
> & {
  id: number;
  fullAnswer?: string;
};
