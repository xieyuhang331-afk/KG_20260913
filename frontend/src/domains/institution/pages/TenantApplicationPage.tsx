import { AlertCircle, ArrowLeft, FileUp, ShieldCheck, Store } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";
import { createTenantApplication } from "../api";
import type { TenantApplicationCreate } from "../types";
import { isApiError } from "@/shared/api/errors";

const DRAFT_KEY = "institution-tenant-application-draft-v1";
const MAX_FILE_SIZE = 10 * 1024 * 1024;
const MEDICAL_TYPES = new Set(["社区医院", "中医诊所", "西医诊所", "综合门诊部"]);

const STORE_TYPES = [
  ["社区医院", "社区医院"],
  ["中医诊所", "中医诊所"],
  ["西医诊所", "西医诊所"],
  ["健康管理门店", "健康管理门店"],
  ["体检中心", "体检中心"],
  ["综合门诊部", "综合门诊部"],
  ["其他", "其他"],
] as const;

type FormValues = {
  name: string;
  type: string;
  creditCode: string;
  licenseNo: string;
  legalPersonName: string;
  province: string;
  city: string;
  district: string;
  address: string;
  contactName: string;
  contactPhone: string;
  contactEmail: string;
};

type SubmissionState =
  | "idle"
  | "validating"
  | "submitting"
  | "validation_error"
  | "conflict"
  | "unavailable"
  | "unknown_error";

const emptyValues: FormValues = {
  name: "",
  type: "",
  creditCode: "",
  licenseNo: "",
  legalPersonName: "",
  province: "",
  city: "",
  district: "",
  address: "",
  contactName: "",
  contactPhone: "",
  contactEmail: "",
};

export function TenantApplicationPage() {
  const navigate = useNavigate();
  const [submissionState, setSubmissionState] = useState<SubmissionState>("idle");
  const [confirmationOpen, setConfirmationOpen] = useState(false);
  const [fileError, setFileError] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const inFlight = useRef(false);
  const confirmationCancelRef = useRef<HTMLButtonElement>(null);
  const confirmationSubmitRef = useRef<HTMLButtonElement>(null);
  const confirmationTriggerRef = useRef<HTMLElement | null>(null);
  const form = useForm<FormValues>({ defaultValues: readDraft() });
  const selectedType = form.watch("type");
  const isMedical = MEDICAL_TYPES.has(selectedType);

  useEffect(() => {
    const subscription = form.watch((values) => {
      const safeDraft = {
        name: values.name ?? "",
        type: values.type ?? "",
        province: values.province ?? "",
        city: values.city ?? "",
        district: values.district ?? "",
        address: values.address ?? "",
      };
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(safeDraft));
    });
    return () => subscription.unsubscribe();
  }, [form]);

  useEffect(() => {
    if (!isMedical) form.setValue("licenseNo", "");
  }, [form, isMedical]);

  useEffect(() => {
    if (confirmationOpen) confirmationCancelRef.current?.focus();
  }, [confirmationOpen]);

  async function prepareSubmission() {
    setSubmissionState("validating");
    const valid = await form.trigger(undefined, { shouldFocus: true });
    if (!valid) {
      setSubmissionState("validation_error");
      return;
    }
    setSubmissionState("idle");
    confirmationTriggerRef.current = document.activeElement as HTMLElement | null;
    setConfirmationOpen(true);
  }

  function closeConfirmation() {
    setConfirmationOpen(false);
    window.requestAnimationFrame(() => confirmationTriggerRef.current?.focus());
  }

  function handleConfirmationKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeConfirmation();
      return;
    }
    if (event.key !== "Tab") return;
    const first = confirmationCancelRef.current;
    const last = confirmationSubmitRef.current;
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function submit(values: FormValues) {
    if (inFlight.current) return;
    inFlight.current = true;
    setConfirmationOpen(false);
    setSubmissionState("submitting");
    const payload: TenantApplicationCreate = {
      name: values.name.trim(),
      type: values.type,
      credit_code: values.creditCode.trim().toUpperCase(),
      license_no: isMedical ? values.licenseNo.trim() : null,
      license_image: null,
      legal_person_name: values.legalPersonName.trim(),
      province: values.province.trim(),
      city: values.city.trim(),
      district: values.district.trim(),
      address: values.address.trim(),
      contact_name: values.contactName.trim(),
      contact_phone: values.contactPhone.trim(),
      contact_email: values.contactEmail.trim(),
      attachments: [],
    };

    try {
      const created = await createTenantApplication(payload);
      sessionStorage.removeItem(DRAFT_KEY);
      setSelectedFiles([]);
      navigate(`/institution/store/application/${created.id}/status`, { replace: true });
    } catch (error) {
      setSubmissionState(mapSubmissionError(error));
    } finally {
      inFlight.current = false;
    }
  }

  function handleFiles(files: FileList | null) {
    setFileError("");
    const next = Array.from(files ?? []);
    if (next.length > 5) {
      setFileError("一次最多选择 5 个文件");
      setSelectedFiles([]);
      return;
    }
    if (next.some((file) => !["image/jpeg", "image/png", "application/pdf"].includes(file.type))) {
      setFileError("仅支持 JPG、PNG 或 PDF 文件");
      setSelectedFiles([]);
      return;
    }
    if (next.some((file) => file.size > MAX_FILE_SIZE)) {
      setFileError("单个文件大小不可超过 10MB");
      setSelectedFiles([]);
      return;
    }
    setSelectedFiles(next);
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white px-5 py-5 shadow-sm sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-medium text-slate-400">门店管理 / 入驻申请</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">门店入驻申请</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
            填写当前合同支持的门店资料。敏感字段和附件不会保存到本地草稿。
          </p>
        </div>
        <Link
          className="inline-flex items-center gap-2 text-sm font-medium text-teal-700 hover:text-teal-800"
          to="/institution/store/applications"
        >
          <ArrowLeft aria-hidden="true" size={16} /> 返回我的申请
        </Link>
      </header>

      <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
        <form
          className="min-w-0 space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            void prepareSubmission();
          }}
          noValidate
        >
          <FormSection icon={<Store size={18} />} title="门店主体信息" description="带 * 的字段为本次提交必填项。">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="门店名称" error={form.formState.errors.name?.message} required>
                <input
                  {...form.register("name", { validate: textLength("请输入 2-30 个字符的门店名称", 2, 30) })}
                  className={inputClass}
                  aria-label="门店名称"
                />
              </Field>
              <Field label="门店类型" error={form.formState.errors.type?.message} required>
                <select
                  {...form.register("type", { required: "请选择门店类型" })}
                  className={inputClass}
                  aria-label="门店类型"
                >
                  <option value="">请选择</option>
                  {STORE_TYPES.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="统一社会信用代码"
                hint="18 位大写字母或数字"
                error={form.formState.errors.creditCode?.message}
                required
              >
                <input
                  {...form.register("creditCode", {
                    required: "请输入 18 位统一社会信用代码",
                    pattern: { value: /^[0-9A-Z]{18}$/i, message: "请输入 18 位统一社会信用代码" },
                  })}
                  className={inputClass}
                  aria-label="统一社会信用代码"
                  autoComplete="off"
                />
              </Field>
              {isMedical ? (
                <Field label="医疗机构许可证号" error={form.formState.errors.licenseNo?.message} required>
                  <input
                    {...form.register("licenseNo", {
                      validate: (value) => value.trim().length > 0 || "医疗类门店必须填写医疗机构许可证号",
                    })}
                    className={inputClass}
                    aria-label="医疗机构许可证号"
                    autoComplete="off"
                  />
                </Field>
              ) : null}
              <Field label="法定代表人" error={form.formState.errors.legalPersonName?.message} required>
                <input
                  {...form.register("legalPersonName", {
                    validate: textLength("请输入 2-20 个字符的法定代表人", 2, 20),
                  })}
                  className={inputClass}
                  aria-label="法定代表人"
                  autoComplete="off"
                />
              </Field>
            </div>
          </FormSection>

          <FormSection
            title="经营地址"
            description="当前合同没有行政区字典接口，V1 采用逐级填写；修改上级会清空下级，避免地区错配。"
          >
            <div className="grid gap-4 md:grid-cols-3">
              <Field label="省" error={form.formState.errors.province?.message} required>
                <input
                  {...form.register("province", {
                    required: "请输入省份",
                    onChange: () => {
                      form.setValue("city", "");
                      form.setValue("district", "");
                    },
                  })}
                  className={inputClass}
                  aria-label="省"
                />
              </Field>
              <Field label="市" error={form.formState.errors.city?.message} required>
                <input
                  {...form.register("city", { required: "请输入城市", onChange: () => form.setValue("district", "") })}
                  className={inputClass}
                  aria-label="市"
                  disabled={!form.watch("province")}
                />
              </Field>
              <Field label="区/县" error={form.formState.errors.district?.message} required>
                <input
                  {...form.register("district", { required: "请输入区/县" })}
                  className={inputClass}
                  aria-label="区/县"
                  disabled={!form.watch("city")}
                />
              </Field>
            </div>
            <Field label="详细地址" error={form.formState.errors.address?.message} required>
              <textarea
                {...form.register("address", { validate: textLength("请输入 5-100 个字符的详细地址", 5, 100) })}
                className={`${inputClass} min-h-24 resize-y py-2.5`}
                aria-label="详细地址"
              />
            </Field>
          </FormSection>

          <FormSection title="申请联系人" description="以下字段只用于本次安全提交，不写入本地草稿。">
            <div className="grid gap-4 md:grid-cols-3">
              <Field label="联系人姓名" error={form.formState.errors.contactName?.message} required>
                <input
                  {...form.register("contactName", { validate: textLength("请输入 2-20 个字符的联系人姓名", 2, 20) })}
                  className={inputClass}
                  aria-label="联系人姓名"
                  autoComplete="off"
                />
              </Field>
              <Field label="联系人手机" error={form.formState.errors.contactPhone?.message} required>
                <input
                  {...form.register("contactPhone", {
                    required: "请输入联系人手机",
                    pattern: { value: /^1[3-9]\d{9}$/, message: "请输入正确的 11 位手机号" },
                  })}
                  className={inputClass}
                  aria-label="联系人手机"
                  inputMode="tel"
                  autoComplete="off"
                />
              </Field>
              <Field label="联系邮箱" error={form.formState.errors.contactEmail?.message} required>
                <input
                  {...form.register("contactEmail", {
                    required: "请输入联系邮箱",
                    pattern: { value: /^[^\s@]+@[^\s@]+\.[^\s@]+$/, message: "请输入正确的联系邮箱" },
                  })}
                  className={inputClass}
                  aria-label="联系邮箱"
                  type="email"
                  autoComplete="off"
                />
              </Field>
            </div>
          </FormSection>

          <FormSection
            icon={<FileUp size={18} />}
            title="证照附件"
            description="可先检查待上传文件，但在正式上传合同提供前不会上传或提交文件。"
          >
            <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-5">
              <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 shadow-sm hover:border-teal-400">
                <FileUp size={16} /> 选择文件
                <input
                  className="sr-only"
                  aria-label="选择证照文件"
                  type="file"
                  multiple
                  accept=".jpg,.jpeg,.png,.pdf"
                  onChange={(event) => handleFiles(event.target.files)}
                />
              </label>
              <p className="mt-3 text-xs text-slate-500">
                JPG / PNG / PDF，单个不超过 10MB，最多 5 个。仅做本地校验，不读取内容。
              </p>
              {selectedFiles.length ? (
                <p className="mt-2 text-sm text-slate-600">已通过格式校验：{selectedFiles.length} 个文件（尚未上传）</p>
              ) : null}
              {fileError ? (
                <p className="mt-2 text-sm font-medium text-red-700" role="alert">
                  {fileError}
                </p>
              ) : null}
              <p className="mt-3 font-mono text-xs font-semibold text-amber-700">
                LICENSE UPLOAD: WAITING FOR BACKEND CONTRACT
              </p>
            </div>
          </FormSection>

          {submissionState !== "idle" && submissionState !== "validating" && submissionState !== "submitting" ? (
            <StatusMessage state={submissionState} />
          ) : null}
          <div className="sticky bottom-3 flex flex-col-reverse gap-3 rounded-xl border border-slate-200 bg-white/95 p-4 shadow-lg backdrop-blur sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-slate-500">提交后进入平台审核；当前合同没有服务端草稿和重新提交接口。</p>
            <button
              className="rounded-lg bg-teal-700 px-5 py-2.5 text-sm font-semibold text-white hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={submissionState === "submitting" || submissionState === "validating"}
              onClick={prepareSubmission}
              type="button"
            >
              {submissionState === "submitting" ? "正在提交" : "核对并提交"}
            </button>
          </div>
        </form>

        <aside className="space-y-4 xl:sticky xl:top-24 xl:self-start">
          <section className="rounded-xl border border-teal-200 bg-teal-50 p-5">
            <div className="flex items-center gap-2 font-semibold text-teal-900">
              <ShieldCheck size={18} /> 提交就绪度
            </div>
            <ul className="mt-4 space-y-3 text-sm text-teal-950/75">
              <li>主体、地址与联系人可真实提交</li>
              <li>敏感字段不保存到本地草稿</li>
              <li>重复点击受到请求锁保护</li>
            </ul>
          </section>
          <section className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-950">
            <h2 className="font-semibold">合同等待项</h2>
            <p className="mt-3 font-mono text-xs font-semibold">SERVICE AREA CONTRACT: WAITING FOR BACKEND</p>
            <p className="mt-3 leading-6 text-amber-900/75">
              主营领域不会被塞入其他字段，也不会在未入库时显示为已保存。
            </p>
          </section>
        </aside>
      </div>

      {confirmationOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-4" role="presentation">
          <section
            aria-label="确认提交门店申请"
            aria-modal="true"
            className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"
            onKeyDown={handleConfirmationKeyDown}
            role="dialog"
          >
            <h2 className="text-lg font-semibold text-slate-950">确认提交门店申请</h2>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              提交后当前合同不支持在线修改或重新提交。附件与主营领域尚未接入，本次只提交核心资料。
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <button
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm"
                onClick={closeConfirmation}
                ref={confirmationCancelRef}
                type="button"
              >
                继续检查
              </button>
              <button
                className="rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white"
                onClick={form.handleSubmit(submit)}
                ref={confirmationSubmitRef}
                type="button"
              >
                确认提交
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

const inputClass =
  "h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-950 outline-none transition focus:border-teal-600 focus:ring-2 focus:ring-teal-100 disabled:cursor-not-allowed disabled:bg-slate-100";

function FormSection({
  title,
  description,
  icon,
  children,
}: {
  title: string;
  description: string;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="border-b border-slate-100 pb-4">
        <h2 className="flex items-center gap-2 font-semibold text-slate-950">
          {icon}
          {title}
        </h2>
        <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  hint,
  error,
  required,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="block min-w-0">
      <span className="mb-1.5 flex items-center gap-1 text-sm font-medium text-slate-700">
        {label}
        {required ? <span className="text-red-600">*</span> : null}
      </span>
      {children}
      {hint && !error ? <span className="mt-1 block text-xs text-slate-400">{hint}</span> : null}
      {error ? (
        <span className="mt-1 block text-xs font-medium text-red-700" role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}

function StatusMessage({ state }: { state: SubmissionState }) {
  const text =
    state === "conflict"
      ? "该统一社会信用代码已有申请，请核对后查看申请记录"
      : state === "validation_error"
        ? "部分字段未通过服务端校验，请检查标记项后重试"
        : state === "unavailable"
          ? "申请服务暂时不可用，已保留填写内容，请稍后重试"
          : "申请提交失败，已保留填写内容，请稍后重试";
  return (
    <div
      className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800"
      role="alert"
    >
      <AlertCircle className="mt-0.5 shrink-0" size={18} />
      <span>{text}</span>
    </div>
  );
}

function textLength(message: string, min: number, max: number) {
  return (value: string) => {
    const length = value.trim().length;
    return (length >= min && length <= max) || message;
  };
}

function readDraft(): FormValues {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(DRAFT_KEY) ?? "{}") as Partial<FormValues>;
    return {
      ...emptyValues,
      name: parsed.name ?? "",
      type: parsed.type ?? "",
      province: parsed.province ?? "",
      city: parsed.city ?? "",
      district: parsed.district ?? "",
      address: parsed.address ?? "",
    };
  } catch {
    return emptyValues;
  }
}

function mapSubmissionError(error: unknown): SubmissionState {
  if (isApiError(error)) {
    if (error.status === 409) return "conflict";
    if (error.status === 422) return "validation_error";
    if (error.status === 503) return "unavailable";
  }
  return "unknown_error";
}
