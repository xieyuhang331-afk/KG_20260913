import { readFile, readdir } from "node:fs/promises";
import { extname, join } from "node:path";

const roots = ["src/domains/platform", "src/domains/organization", "src/domains/institution"];
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
      const portableFile = file.replaceAll("\\", "/");
      const isApplicationDraftFile = portableFile.endsWith("domains/institution/pages/TenantApplicationPage.tsx");
      const isApplicationDraftTest = portableFile.endsWith("domains/institution/门店入驻申请表.test.tsx");
      if (label === "敏感浏览器存储" && isApplicationDraftFile) {
        validateApplicationDraftStorage(file, content);
        continue;
      }
      if (label === "敏感浏览器存储" && isApplicationDraftTest) {
        validateApplicationDraftTestStorage(file, content);
        continue;
      }
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
  "PII scan passed: platform, organization and institution frontend source contains no forbidden sensitive values, logs, snapshots or token literals.",
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

function validateApplicationDraftStorage(file, content) {
  if (/\blocalStorage\b/.test(content)) findings.push(`申请草稿禁止localStorage: ${file}`);
  const storageCalls = [...content.matchAll(/sessionStorage\.(\w+)\s*\(/g)];
  const allowedMethodCounts = { setItem: 1, removeItem: 1, getItem: 1 };
  for (const method of Object.keys(allowedMethodCounts)) {
    const actual = storageCalls.filter((match) => match[1] === method).length;
    if (actual !== allowedMethodCounts[method]) findings.push(`申请草稿存储调用数量异常: ${file}`);
  }
  if (storageCalls.some((match) => !(match[1] in allowedMethodCounts))) {
    findings.push(`申请草稿非白名单存储调用: ${file}`);
  }
  for (const requiredFragment of [
    "sessionStorage.setItem(DRAFT_KEY, JSON.stringify(safeDraft))",
    "sessionStorage.removeItem(DRAFT_KEY)",
    "sessionStorage.getItem(DRAFT_KEY)",
  ]) {
    if (!content.includes(requiredFragment)) findings.push(`申请草稿存储参数不符合白名单: ${file}`);
  }
  const draftBlock = content.match(/const safeDraft = \{([\s\S]*?)\n\s*\};/);
  const forbiddenDraftFields =
    /\b(?:creditCode|licenseNo|legalPersonName|contactName|contactPhone|contactEmail|selectedFiles|attachments)\b/;
  if (!draftBlock || forbiddenDraftFields.test(draftBlock[1])) {
    findings.push(`申请草稿包含敏感字段或白名单不可验证: ${file}`);
  }
}

function validateApplicationDraftTestStorage(file, content) {
  if (/\blocalStorage\b/.test(content)) findings.push(`申请测试禁止localStorage: ${file}`);
  const allowedMethods = new Set(["clear", "getItem"]);
  for (const match of content.matchAll(/sessionStorage\.(\w+)\s*\(/g)) {
    if (!allowedMethods.has(match[1])) findings.push(`申请测试非白名单存储调用: ${file}`);
  }
}
