import type { Question, Topic } from "@/data/types";

export type AilianIntro = {
  candidate: string;
  title: string;
  body: string;
};

export type BankStats = {
  totalParents: number;
  totalQuestions: number;
  alibabaParents: number;
  ailianParents: number;
  alibabaTopics: number;
  ailianTopics: number;
};

export type CandidateProfile = {
  name: string;
  from: string;
  to: string;
  oneLiner: string;
  interviewerFrame: string;
};

export type ClientBank = {
  topics: Topic[];
  questions: Question[];
  ailianIntro: AilianIntro;
  candidate: CandidateProfile | null;
  stats: BankStats & { fromFiles: true };
};
