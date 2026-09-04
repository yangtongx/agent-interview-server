import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { loadBank, publicBank } from "../src/lib/bank-store";

const out = path.join(process.cwd(), "data", "public-bank.json");
mkdirSync(path.dirname(out), { recursive: true });
const bank = publicBank(loadBank(true));
writeFileSync(out, JSON.stringify(bank), "utf8");
console.log(
  `wrote ${out} parents=${bank.stats.totalParents} questions=${bank.stats.totalQuestions}`,
);
