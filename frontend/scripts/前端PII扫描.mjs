import { readFile, readdir } from "node:fs/promises";
import { extname, join } from "node:path";

const roots = ["src/domains/platform"];
const allowedExtensions = new Set([".ts", ".tsx"]);
const forbiddenPatterns = [
  ["完整身份证号", /(?<!\d)\d{17}[0-9Xx](?!\w)/g],
  ["完整身份证字段", /\bid_card\b/g],
  ["Bearer Token", /\bBearer\s+[A-Za-z0-9._~-]+/g],
  ["JWT", /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g],
];

const findings = [];

for (const root of roots) {
  for (const file of await listFiles(root)) {
    if (!allowedExtensions.has(extname(file))) continue;
    const content = await readFile(file, "utf8");
    for (const [label, pattern] of forbiddenPatterns) {
      pattern.lastIndex = 0;
      if (pattern.test(content)) findings.push(`${label}: ${file}`);
    }
  }
}

if (findings.length > 0) {
  console.error(`PII scan failed:\n${findings.join("\n")}`);
  process.exit(1);
}

console.log("PII scan passed: platform frontend source contains no forbidden identity values or token literals.");

async function listFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await listFiles(path)));
    else files.push(path);
  }
  return files;
}
