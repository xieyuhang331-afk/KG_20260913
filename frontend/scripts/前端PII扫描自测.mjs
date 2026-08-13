import { readFile, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";

const target = "src/domains/institution/pages/TenantApplicationPage.tsx";
const original = await readFile(target, "utf8");
const marker = "const safeDraft = {";
if (!original.includes(marker)) throw new Error("application draft marker missing");

try {
  await writeFile(
    target,
    original.replace(marker, `${marker}\n        contactPhone: values.contactPhone ?? "",`),
    "utf8",
  );
  const result = spawnSync(process.execPath, ["scripts/前端PII扫描.mjs"], { encoding: "utf8" });
  if (result.status === 0 || !result.stderr.includes("申请草稿包含敏感字段")) {
    throw new Error("PII scan negative control was not detected");
  }
  console.log("PII scan negative control passed: sensitive draft storage is rejected.");
} finally {
  await writeFile(target, original, "utf8");
}
