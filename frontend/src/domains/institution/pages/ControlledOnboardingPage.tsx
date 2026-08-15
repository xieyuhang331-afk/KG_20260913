import { useEffect, useState } from "react";
import {
  completePrivateFileUpload,
  getOnboardingApplication,
  getPrivateFileMetadata,
  initiatePrivateFileUpload,
  resubmitOnboardingApplication,
  saveOnboardingDraft,
  submitOnboardingApplication,
  uploadPrivateFileContent,
  type LicenseBindingPayload,
  type OnboardingApplication,
} from "../api";

async function fileSha256(file: File) {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function isUploadFile(value: FormDataEntryValue | null): value is File {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<File>;
  return (
    typeof candidate.name === "string" &&
    typeof candidate.type === "string" &&
    typeof candidate.size === "number" &&
    Number.isSafeInteger(candidate.size) &&
    candidate.size > 0 &&
    typeof candidate.arrayBuffer === "function"
  );
}

async function uploadCleanFile(file: File, purpose: LicenseBindingPayload["license_type"]) {
  const sha256 = await fileSha256(file);
  const initiated = await initiatePrivateFileUpload({
    purpose,
    size: file.size,
    mime_type: file.type,
    sha256,
  });
  await uploadPrivateFileContent(initiated.file_id, file);
  await completePrivateFileUpload(initiated.file_id, {
    size: file.size,
    mime_type: file.type,
    sha256,
  });
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const metadata = await getPrivateFileMetadata(initiated.file_id);
    if (metadata.status === "CLEAN") return initiated.file_id;
    if (["REJECTED", "SCAN_FAILED"].includes(metadata.status)) throw new Error("材料扫描未通过");
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  throw new Error("材料仍在扫描，请稍后重试");
}

export function ControlledOnboardingPage() {
  const [application, setApplication] = useState<OnboardingApplication | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [businessFile, setBusinessFile] = useState<File | null>(null);
  const [medicalFile, setMedicalFile] = useState<File | null>(null);
  useEffect(() => {
    void getOnboardingApplication()
      .then(setApplication)
      .catch((error) => setMessage(error instanceof Error ? error.message : "读取失败"));
  }, []);
  if (!application)
    return (
      <main>
        <h1 className="text-2xl font-semibold">机构受控入驻</h1>
        <p role="status">{message || "加载中"}</p>
      </main>
    );
  const currentApplication = application;
  const draft = currentApplication.draft ?? {};
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const intent = ((event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null)?.value;
    const data = new window.FormData(form);
    const payload = {
      credit_code: String(data.get("credit_code") ?? ""),
      legal_representative_name: String(data.get("legal_representative_name") ?? ""),
      registered_address: String(data.get("registered_address") ?? ""),
      service_address: String(data.get("service_address") ?? ""),
      contact_name: String(data.get("contact_name") ?? ""),
      contact_phone: String(data.get("contact_phone") ?? ""),
      contact_email: String(data.get("contact_email") ?? ""),
      service_tags: String(data.get("service_tags") ?? "")
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
    };
    setBusy(true);
    try {
      if (intent === "save") {
        setApplication(
          await saveOnboardingDraft({
            ...payload,
            expected_version: Number(currentApplication.version),
          }),
        );
        setMessage("草稿已保存");
        return;
      }
      let licenses: LicenseBindingPayload[] = currentApplication.licenses ?? [];
      let expectedVersion = Number(currentApplication.version);
      if (currentApplication.status === "DRAFT") {
        const saved = await saveOnboardingDraft({
          ...payload,
          expected_version: expectedVersion,
        });
        expectedVersion = Number(saved.version);
        if (!isUploadFile(businessFile)) throw new Error("请选择营业执照");
        licenses = [
          {
            license_type: "BUSINESS_LICENSE",
            private_file_id: await uploadCleanFile(businessFile, "BUSINESS_LICENSE"),
          },
        ];
        if (currentApplication.institution_type === "LICENSED_CLINIC") {
          if (!isUploadFile(medicalFile)) throw new Error("请选择医疗机构执业许可证");
          licenses.push({
            license_type: "MEDICAL_INSTITUTION_LICENSE",
            private_file_id: await uploadCleanFile(medicalFile, "MEDICAL_INSTITUTION_LICENSE"),
          });
        }
        setApplication(
          await submitOnboardingApplication({ expected_version: expectedVersion, licenses }, crypto.randomUUID()),
        );
      } else if (currentApplication.status === "NEEDS_CORRECTION") {
        if (currentApplication.correction_fields?.includes("business_license")) {
          if (!isUploadFile(businessFile)) throw new Error("请选择新的营业执照");
          licenses = licenses.filter((value) => value.license_type !== "BUSINESS_LICENSE");
          licenses.push({
            license_type: "BUSINESS_LICENSE",
            private_file_id: await uploadCleanFile(businessFile, "BUSINESS_LICENSE"),
          });
        }
        if (currentApplication.correction_fields?.includes("medical_institution_license")) {
          if (!isUploadFile(medicalFile)) throw new Error("请选择新的医疗机构执业许可证");
          licenses = licenses.filter((value) => value.license_type !== "MEDICAL_INSTITUTION_LICENSE");
          licenses.push({
            license_type: "MEDICAL_INSTITUTION_LICENSE",
            private_file_id: await uploadCleanFile(medicalFile, "MEDICAL_INSTITUTION_LICENSE"),
          });
        }
        setApplication(
          await resubmitOnboardingApplication(
            { ...payload, expected_version: expectedVersion, licenses },
            crypto.randomUUID(),
          ),
        );
      } else {
        throw new Error("当前状态不可提交");
      }
      setMessage("申请已提交");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提交失败");
    } finally {
      setBusy(false);
    }
  }
  const fields = [
    ["credit_code", "统一社会信用代码"],
    ["legal_representative_name", "法定代表人"],
    ["registered_address", "注册地址"],
    ["service_address", "服务地址"],
    ["contact_name", "联系人"],
    ["contact_phone", "联系电话"],
    ["contact_email", "联系邮箱"],
    ["service_tags", "服务标签（逗号分隔）"],
  ];
  return (
    <main>
      <h1 className="text-2xl font-semibold">机构受控入驻</h1>
      <p className="mt-2 text-sm">状态：{String(application.status)}</p>
      {application.status === "NEEDS_CORRECTION" && (
        <p className="mt-2 text-sm">补正原因：{String(application.correction_reason_code ?? "待补正")}</p>
      )}
      <form className="mt-6 grid gap-3 md:grid-cols-2" onSubmit={submit}>
        {fields.map(([name, label]) => (
          <label key={name}>
            {label}
            <input
              name={name}
              defaultValue={
                Array.isArray(draft[name]) ? (draft[name] as string[]).join(",") : String(draft[name] ?? "")
              }
              required
              className="mt-1 w-full rounded border p-2"
            />
          </label>
        ))}
        {(application.status === "DRAFT" || application.correction_fields?.includes("business_license")) && (
          <label>
            营业执照
            <input
              aria-label="营业执照"
              name="business_license"
              type="file"
              accept="application/pdf,image/jpeg,image/png"
              required
              onChange={(event) => setBusinessFile(event.currentTarget.files?.[0] ?? null)}
            />
          </label>
        )}
        {application.institution_type === "LICENSED_CLINIC" &&
          (application.status === "DRAFT" ||
            application.correction_fields?.includes("medical_institution_license")) && (
            <label>
              医疗机构执业许可证
              <input
                name="medical_license"
                type="file"
                accept="application/pdf,image/jpeg,image/png"
                required
                onChange={(event) => setMedicalFile(event.currentTarget.files?.[0] ?? null)}
              />
            </label>
          )}
        <div className="flex gap-2 md:col-span-2">
          {application.status === "DRAFT" && (
            <button type="submit" disabled={busy} name="intent" value="save" className="rounded border px-4 py-2">
              保存草稿
            </button>
          )}
          <button
            type="submit"
            disabled={busy || !["DRAFT", "NEEDS_CORRECTION"].includes(String(application.status))}
            name="intent"
            value="submit"
            className="rounded bg-pine px-4 py-2 text-white"
          >
            {application.status === "NEEDS_CORRECTION" ? "补正并重新提交" : "上传材料并提交"}
          </button>
        </div>
      </form>
      {message && <p role="status">{message}</p>}
    </main>
  );
}
