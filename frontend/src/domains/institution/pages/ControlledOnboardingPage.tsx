import { FileCheck2, RefreshCw, Save, Send, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { isApiError } from "@/shared/api/errors";
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
import {
  ConfirmDialog,
  EmptyPanel,
  Feedback,
  LoadingPanel,
  applicationStatusClassName,
  applicationStatusLabel,
  fieldClassName,
  onboardingErrorMessage,
  primaryButtonClassName,
  secondaryButtonClassName,
} from "../受控入驻界面";

const allowedMimeTypes = new Set(["application/pdf", "image/jpeg", "image/png"]);
const maxFileSize = 10 * 1024 * 1024;

async function fileSha256(file: File) {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

function validateUploadFile(file: File | null, label: string): File {
  if (!file || file.size <= 0) throw new Error(`请选择${label}`);
  if (!allowedMimeTypes.has(file.type)) throw new Error(`${label}仅支持 PDF、JPG 或 PNG`);
  if (file.size > maxFileSize) throw new Error(`${label}不能超过 10MB`);
  return file;
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
    if (metadata.status === "REJECTED") throw new Error("材料安全扫描未通过，请更换文件后重试。");
    if (metadata.status === "SCAN_FAILED") throw new Error("材料扫描失败，请稍后重新上传。");
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
  }
  throw new Error("材料仍在扫描，请稍后重试。");
}

export function ControlledOnboardingPage() {
  const formRef = useRef<HTMLFormElement>(null);
  const [application, setApplication] = useState<OnboardingApplication | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [tone, setTone] = useState<"success" | "error">("success");
  const [busy, setBusy] = useState(false);
  const [businessFile, setBusinessFile] = useState<File | null>(null);
  const [medicalFile, setMedicalFile] = useState<File | null>(null);
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      setApplication(await getOnboardingApplication());
    } catch (error) {
      setLoadError(onboardingErrorMessage(error, "申请读取失败，请重试。"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      setBusinessFile(null);
      setMedicalFile(null);
    };
  }, [load]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !application) return;
    const intent = ((event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null)?.value;
    if (intent === "save") {
      await executeMutation("save");
      return;
    }
    setConfirming(true);
  }

  async function executeMutation(intent: "save" | "submit") {
    if (!application || !formRef.current || busy) return;
    setBusy(true);
    setMessage("");
    const currentApplication = application;
    const data = new window.FormData(formRef.current);
    const existingDraft = currentApplication.draft;
    const textValue = (name: string) => String(data.get(name) ?? existingDraft[name] ?? "").trim();
    const serviceTagsValue = data.get("service_tags");
    const payload = {
      credit_code: textValue("credit_code"),
      legal_representative_name: textValue("legal_representative_name"),
      registered_address: textValue("registered_address"),
      service_address: textValue("service_address"),
      contact_name: textValue("contact_name"),
      contact_phone: textValue("contact_phone"),
      contact_email: textValue("contact_email"),
      service_tags:
        serviceTagsValue === null
          ? Array.isArray(existingDraft.service_tags)
            ? existingDraft.service_tags
            : []
          : String(serviceTagsValue)
              .split(",")
              .map((value) => value.trim())
              .filter(Boolean),
    };
    try {
      if (intent === "save") {
        setApplication(
          await saveOnboardingDraft({
            ...payload,
            expected_version: currentApplication.version,
          }),
        );
        setTone("success");
        setMessage("草稿已安全保存。");
        return;
      }
      let licenses: LicenseBindingPayload[] = currentApplication.licenses ?? [];
      let expectedVersion = currentApplication.version;
      if (currentApplication.status === "DRAFT") {
        const saved = await saveOnboardingDraft({
          ...payload,
          expected_version: expectedVersion,
        });
        expectedVersion = saved.version;
        const business = validateUploadFile(businessFile, "营业执照");
        licenses = [
          {
            license_type: "BUSINESS_LICENSE",
            private_file_id: await uploadCleanFile(business, "BUSINESS_LICENSE"),
          },
        ];
        if (currentApplication.institution_type === "LICENSED_CLINIC") {
          const medical = validateUploadFile(medicalFile, "医疗机构执业许可证");
          licenses.push({
            license_type: "MEDICAL_INSTITUTION_LICENSE",
            private_file_id: await uploadCleanFile(medical, "MEDICAL_INSTITUTION_LICENSE"),
          });
        }
        setApplication(
          await submitOnboardingApplication({ expected_version: expectedVersion, licenses }, crypto.randomUUID()),
        );
      } else if (currentApplication.status === "NEEDS_CORRECTION") {
        if (currentApplication.correction_fields?.includes("business_license")) {
          const business = validateUploadFile(businessFile, "新的营业执照");
          licenses = licenses.filter((value) => value.license_type !== "BUSINESS_LICENSE");
          licenses.push({
            license_type: "BUSINESS_LICENSE",
            private_file_id: await uploadCleanFile(business, "BUSINESS_LICENSE"),
          });
        }
        if (currentApplication.correction_fields?.includes("medical_institution_license")) {
          const medical = validateUploadFile(medicalFile, "新的医疗机构执业许可证");
          licenses = licenses.filter((value) => value.license_type !== "MEDICAL_INSTITUTION_LICENSE");
          licenses.push({
            license_type: "MEDICAL_INSTITUTION_LICENSE",
            private_file_id: await uploadCleanFile(medical, "MEDICAL_INSTITUTION_LICENSE"),
          });
        }
        setApplication(
          await resubmitOnboardingApplication(
            { ...payload, expected_version: expectedVersion, licenses },
            crypto.randomUUID(),
          ),
        );
      } else {
        throw new Error("当前状态不能提交，请刷新页面确认最新状态。");
      }
      setBusinessFile(null);
      setMedicalFile(null);
      setTone("success");
      setMessage(
        currentApplication.status === "NEEDS_CORRECTION"
          ? "补正已重新提交，等待平台复核。"
          : "申请已提交，等待平台审核。",
      );
    } catch (error) {
      setTone("error");
      setMessage(
        error instanceof Error && !isApiError(error)
          ? error.message
          : onboardingErrorMessage(error, "提交失败，已填写内容仍保留在页面中。"),
      );
      if (isApiError(error) && error.status === 409) await load();
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  }

  if (loading) return <LoadingPanel label="正在读取机构入驻申请…" />;
  if (loadError || !application)
    return (
      <EmptyPanel
        action={
          <button className={secondaryButtonClassName} onClick={() => void load()} type="button">
            <RefreshCw aria-hidden="true" className="mr-2" size={16} />
            重试
          </button>
        }
        description={loadError || "没有可用申请。请确认账号由有效邀请激活。"}
        title="申请暂时不可用"
      />
    );

  const draft = application.draft ?? {};
  const editable = ["DRAFT", "NEEDS_CORRECTION"].includes(application.status);
  return (
    <main className="space-y-5">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold tracking-[0.16em] text-teal-700">INSTITUTION ONBOARDING</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">机构受控入驻</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
            填写机构资料、通过私有材料安全扫描，并跟踪平台审核结果。
          </p>
        </div>
        <span
          className={`self-start rounded-full px-3 py-1.5 text-sm font-semibold ring-1 ${applicationStatusClassName(application.status)}`}
        >
          {applicationStatusLabel(application.status)}
        </span>
      </header>

      <OnboardingProgress status={application.status} />
      <Feedback message={message} tone={tone} />

      {application.status === "APPROVED" ? (
        <ServiceReadinessCard serviceReady={application.service_ready} tenantActive={application.tenant_active} />
      ) : application.status === "REJECTED" ? (
        <EmptyPanel
          description="该申请已结束，不能在当前记录上继续补正。如需重新入驻，请联系平台确认后续流程。"
          title="申请未通过"
        />
      ) : ["SUBMITTED", "UNDER_REVIEW"].includes(application.status) ? (
        <EmptyPanel
          description="平台正在审核本次申请。状态变化后可刷新页面查看；审核期间不会重复提交。"
          title={application.status === "UNDER_REVIEW" ? "申请审核中" : "申请已进入审核队列"}
        />
      ) : (
        <form
          className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_340px]"
          onSubmit={handleSubmit}
          ref={formRef}
        >
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel sm:p-6">
            <div>
              <h2 className="font-semibold text-slate-950">机构基础资料</h2>
              <p className="mt-1 text-xs text-slate-500">带 * 的字段为当前合同必填项。补正状态下仅修改平台指定字段。</p>
            </div>
            {application.status === "NEEDS_CORRECTION" ? (
              <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
                <strong>补正要求：</strong>
                {correctionLabels(application.correction_fields).join("、") || "请按平台说明修改"}
                <span className="mt-1 block text-xs">
                  原因代码：
                  {application.correction_reason_code || "CORRECTION_REQUIRED"}
                </span>
              </div>
            ) : null}
            <div className="mt-5 grid gap-5 sm:grid-cols-2">
              {fieldDefinitions.map(({ name, label, type, pattern, hint }) => {
                const disabled =
                  application.status === "NEEDS_CORRECTION" && !application.correction_fields.includes(name);
                const value = Array.isArray(draft[name])
                  ? (draft[name] as string[]).join(",")
                  : String(draft[name] ?? "");
                return (
                  <label className="text-sm font-medium text-slate-700" key={name}>
                    {label} *
                    <input
                      aria-label={label}
                      className={fieldClassName}
                      defaultValue={value}
                      disabled={disabled || busy}
                      name={name}
                      pattern={pattern}
                      required
                      type={type ?? "text"}
                    />
                    {hint ? (
                      <span className="mt-1.5 block text-xs font-normal leading-5 text-slate-500">{hint}</span>
                    ) : null}
                  </label>
                );
              })}
            </div>
          </section>

          <aside className="space-y-5 xl:sticky xl:top-24">
            <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
              <div className="flex items-start gap-3">
                <span className="rounded-lg bg-teal-50 p-2 text-teal-700">
                  <FileCheck2 aria-hidden="true" size={19} />
                </span>
                <div>
                  <h2 className="font-semibold text-slate-950">私有证照材料</h2>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    仅支持 PDF/JPG/PNG，单个不超过 10MB。扫描通过后才能提交。
                  </p>
                </div>
              </div>
              <div className="mt-4 space-y-4">
                {application.status === "DRAFT" || application.correction_fields?.includes("business_license") ? (
                  <FileField
                    busy={busy}
                    label={application.status === "DRAFT" ? "营业执照 *" : "重新上传营业执照 *"}
                    name="business_license"
                    onChange={setBusinessFile}
                  />
                ) : (
                  <ExistingMaterial label="营业执照" />
                )}
                {application.institution_type === "LICENSED_CLINIC" ? (
                  application.status === "DRAFT" ||
                  application.correction_fields?.includes("medical_institution_license") ? (
                    <FileField
                      busy={busy}
                      label={application.status === "DRAFT" ? "医疗机构执业许可证 *" : "重新上传医疗机构执业许可证 *"}
                      name="medical_license"
                      onChange={setMedicalFile}
                    />
                  ) : (
                    <ExistingMaterial label="医疗机构执业许可证" />
                  )
                ) : null}
              </div>
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
              <h2 className="font-semibold text-slate-950">提交前确认</h2>
              <p className="mt-2 text-xs leading-5 text-slate-500">
                提交期间所有操作会被锁定。扫描失败、冲突或服务不可用时不会自动重复提交。
              </p>
              <div className="mt-4 flex flex-col gap-3">
                {application.status === "DRAFT" ? (
                  <button className={secondaryButtonClassName} disabled={busy} name="intent" type="submit" value="save">
                    <Save aria-hidden="true" className="mr-2" size={16} />
                    {busy ? "处理中…" : "保存草稿"}
                  </button>
                ) : null}
                <button
                  className={primaryButtonClassName}
                  disabled={busy || !editable}
                  name="intent"
                  type="submit"
                  value="submit"
                >
                  <Send aria-hidden="true" className="mr-2" size={16} />
                  {busy ? "处理中…" : application.status === "NEEDS_CORRECTION" ? "补正并重新提交" : "上传材料并提交"}
                </button>
              </div>
            </section>
          </aside>
        </form>
      )}

      {confirming ? (
        <ConfirmDialog
          busy={busy}
          confirmLabel={application.status === "NEEDS_CORRECTION" ? "确认重新提交" : "确认上传并提交"}
          description={
            application.status === "NEEDS_CORRECTION"
              ? "仅平台指定的补正字段会被接受；提交后将重新进入审核队列。"
              : "系统将先上传材料并等待安全扫描；只有扫描通过后才会正式提交申请。"
          }
          onClose={() => setConfirming(false)}
          onConfirm={() => void executeMutation("submit")}
          title={application.status === "NEEDS_CORRECTION" ? "确认提交补正？" : "确认提交入驻申请？"}
        />
      ) : null}
    </main>
  );
}

interface FieldDefinition {
  name: string;
  label: string;
  pattern?: string;
  type?: string;
  hint?: string;
}

const fieldDefinitions: FieldDefinition[] = [
  { name: "credit_code", label: "统一社会信用代码", pattern: ".{8,32}" },
  {
    name: "legal_representative_name",
    label: "法定代表人",
    pattern: ".{2,50}",
  },
  { name: "registered_address", label: "注册地址", pattern: ".{4,255}" },
  { name: "service_address", label: "服务地址", pattern: ".{4,255}" },
  { name: "contact_name", label: "联系人", pattern: ".{2,50}" },
  { name: "contact_phone", label: "联系电话", pattern: "1[3-9][0-9]{9}" },
  { name: "contact_email", label: "联系邮箱", type: "email" },
  {
    name: "service_tags",
    label: "服务标签（逗号分隔）",
    hint: "多个标签使用英文逗号分隔。",
  },
];

function FileField({
  label,
  name,
  busy,
  onChange,
}: {
  label: string;
  name: string;
  busy: boolean;
  onChange: (file: File | null) => void;
}) {
  return (
    <label className="block text-sm font-medium text-slate-700">
      {label}
      <input
        accept="application/pdf,image/jpeg,image/png"
        aria-label={name === "business_license" ? "营业执照" : "医疗机构执业许可证"}
        className="mt-2 block w-full text-xs text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-teal-50 file:px-3 file:py-2 file:font-semibold file:text-teal-700"
        disabled={busy}
        name={name}
        onChange={(event) => onChange(event.currentTarget.files?.[0] ?? null)}
        type="file"
      />
    </label>
  );
}

function ExistingMaterial({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-3 text-sm text-emerald-800">
      <ShieldCheck aria-hidden="true" size={17} />
      {label}沿用上一修订
    </div>
  );
}

function OnboardingProgress({ status }: { status: string }) {
  const terminalStep =
    status === "NEEDS_CORRECTION" ? "NEEDS_CORRECTION" : status === "REJECTED" ? "REJECTED" : "APPROVED";
  const steps = ["DRAFT", "SUBMITTED", "UNDER_REVIEW", terminalStep];
  const current = Math.max(0, steps.indexOf(status));
  return (
    <ol
      aria-label="入驻进度"
      className="grid gap-2 rounded-xl border border-slate-200 bg-white p-4 shadow-panel sm:grid-cols-4"
    >
      {steps.map((step, index) => (
        <li
          className={`rounded-lg px-3 py-2 text-sm ${index <= current ? "bg-teal-50 font-semibold text-teal-800" : "bg-slate-50 text-slate-400"}`}
          key={step}
        >
          <span className="mr-2 font-mono text-xs">{index + 1}</span>
          {applicationStatusLabel(step)}
        </li>
      ))}
    </ol>
  );
}

function ServiceReadinessCard({ tenantActive, serviceReady }: { tenantActive: boolean; serviceReady: boolean }) {
  return (
    <section className="rounded-xl border border-emerald-200 bg-white p-6 shadow-panel">
      <div className="flex items-start gap-3">
        <span className="rounded-xl bg-emerald-50 p-3 text-emerald-700">
          <ShieldCheck aria-hidden="true" size={22} />
        </span>
        <div>
          <h2 className="text-lg font-semibold text-slate-950">机构已通过入驻审核</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            Tenant 状态：<strong>{tenantActive ? "已激活" : "待激活"}</strong>
          </p>
          <p className="mt-1 text-sm leading-6 text-slate-600">
            服务就绪：<strong>{serviceReady ? "已就绪" : "尚未就绪"}</strong>
          </p>
          {!serviceReady ? (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
              机构账号和 Tenant 已可用，但健康服务尚未开放。请等待后续资质与服务准备流程完成。
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function correctionLabels(fields: string[]) {
  const labels = new Map<string, string>(fieldDefinitions.map((field) => [field.name, field.label]));
  labels.set("business_license", "营业执照");
  labels.set("medical_institution_license", "医疗机构执业许可证");
  return fields.map((field) => labels.get(field) ?? field);
}
