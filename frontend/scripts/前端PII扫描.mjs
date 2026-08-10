import { readFile, readdir } from "node:fs/promises";
import { extname, join } from "node:path";

const roots = ["src/domains/platform", "src/domains/organization"];
const allowedExtensions = new Set([".ts", ".tsx"]);
const forbiddenPatterns = [
  ["完整身份证号", /(?<!\d)\d{17}[0-9Xx](?!\w)/g],
  ["Bearer Token", /\bBearer\s+[A-Za-z0-9._~-]+/g],
  ["JWT", /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g],
  ["敏感浏览器存储", /\b(?:localStorage|sessionStorage)\b/g],
  ["敏感URL参数", /[?&](?:step_up_token|id_card|password)=/gi],
  ["日志输出", /\bconsole\.(?:debug|info|log|warn|error)\s*\(/g],
  ["测试快照", /\btoMatch(?:Inline)?Snapshot\s*\(/g],
  ["错误报告", /\b(?:captureException|captureMessage|reportError)\s*\(/g],
];
const organizationProductionPiiPattern =
  /\b(?:contact|phone|id_card|email|address|password|credit_code|license|legal_person)\b/g;

const findings = [];

for (const root of roots) {
  for (const file of await listFiles(root)) {
    if (!allowedExtensions.has(extname(file))) continue;
    const content = await readFile(file, "utf8");
    for (const [label, pattern] of forbiddenPatterns) {
      pattern.lastIndex = 0;
      if (pattern.test(content)) findings.push(`${label}: ${file}`);
    }
    const portableFile = file.replaceAll("\\", "/");
    if (portableFile.includes("domains/organization") && !portableFile.endsWith(".test.tsx")) {
      organizationProductionPiiPattern.lastIndex = 0;
      if (organizationProductionPiiPattern.test(content)) findings.push(`组织PII字段: ${file}`);
    }
  }
}

if (findings.length > 0) {
  console.error(`PII scan failed:\n${findings.join("\n")}`);
  process.exit(1);
}

console.log(
  "PII scan passed: platform and organization frontend source contains no forbidden sensitive values or token literals.",
);

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
