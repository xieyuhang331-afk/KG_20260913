import { AlertCircle, CheckCircle2, LoaderCircle, X } from "lucide-react";
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { isApiError } from "@/shared/api/errors";

export const fieldClassName =
  "mt-1.5 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-950 outline-none transition focus:border-teal-600 focus:ring-2 focus:ring-teal-100 disabled:cursor-not-allowed disabled:bg-slate-100";

export const secondaryButtonClassName =
  "inline-flex min-h-10 items-center justify-center rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 transition hover:border-teal-300 hover:text-teal-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 disabled:cursor-not-allowed disabled:opacity-50";

export const primaryButtonClassName =
  "inline-flex min-h-10 items-center justify-center rounded-lg bg-teal-700 px-4 text-sm font-semibold text-white transition hover:bg-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

const errorCodeMessages: Record<string, string> = {
  ONBOARDING_INVITATION_EXPIRED: "邀请已过期，请联系平台重新发送。",
  ONBOARDING_INVITATION_ATTEMPT_LIMIT: "尝试次数已达上限，请联系平台重新发送邀请。",
  ONBOARDING_INVITATION_PHONE_MISMATCH: "手机号与邀请不一致，请核对后重试。",
  ONBOARDING_INVITATION_CODE_MISMATCH: "一次性短码不正确，请核对后重试。",
  ONBOARDING_TOTP_INVALID: "动态验证码无效或已过期，请使用当前验证码重试。",
  ONBOARDING_INVITATION_STATE_CONFLICT: "邀请状态已经变化，请刷新后重试。",
  ONBOARDING_INVITATION_VERSION_CONFLICT: "邀请已被其他操作更新，页面已刷新，请重新确认。",
  ONBOARDING_VERSION_CONFLICT: "申请内容已在其他位置更新，页面已刷新，请重新确认。",
  ONBOARDING_APPLICATION_STATE_CONFLICT: "申请状态已经变化，页面已刷新，请按最新状态继续。",
  ONBOARDING_ADMINISTRATIVE_REGION_INVALID: "请选择有效且已启用的区/县组织。",
  ONBOARDING_DRAFT_INCOMPLETE: "申请资料尚未填写完整，请检查必填项。",
  ONBOARDING_REQUIRED_LICENSE_MISSING: "请上传当前机构类型要求的全部证照。",
  ONBOARDING_REQUIRED_CLEAN_FILES_MISSING: "证照尚未通过安全扫描，暂时不能批准。",
  PRIVATE_FILE_NOT_CLEAN: "材料尚未通过安全扫描，请稍后重试。",
  PRIVATE_FILE_SCANNER_UNAVAILABLE: "材料扫描服务暂时不可用，请稍后重试。",
  PRIVATE_FILE_REAUTH_REQUIRED: "当前账户密码验证失败，请重新输入。",
  PRIVATE_FILE_ACCESS_INVALID: "材料查看授权已失效，请重新验证密码。",
};

export function onboardingErrorMessage(error: unknown, fallback: string): string {
  if (!isApiError(error)) return fallback;
  const code = error.message.trim();
  if (errorCodeMessages[code]) return errorCodeMessages[code];
  if (error.status === 401) return "登录状态已失效，请重新登录后继续。";
  if (error.status === 403) return "当前账号没有执行此操作的权限。";
  if (error.status === 404) return "目标记录不存在或已不在当前权限范围内。";
  if (error.status === 409) return "数据已发生变化，页面已刷新，请重新确认。";
  if (error.status === 422) return "提交内容未通过校验，请检查标注的字段。";
  if (error.status === 429) return "操作过于频繁，请稍后再试。";
  if (error.status === 503) return "服务暂时不可用，当前操作未自动重试。请稍后刷新确认。";
  return fallback;
}

export function Feedback({ message, tone = "info" }: { message: string; tone?: "info" | "success" | "error" }) {
  if (!message) return null;
  const classes =
    tone === "error"
      ? "border-red-200 bg-red-50 text-red-800"
      : tone === "success"
        ? "border-emerald-200 bg-emerald-50 text-emerald-800"
        : "border-blue-200 bg-blue-50 text-blue-800";
  const Icon = tone === "success" ? CheckCircle2 : AlertCircle;
  return (
    <div
      className={`flex items-start gap-2 rounded-lg border px-4 py-3 text-sm ${classes}`}
      role={tone === "error" ? "alert" : "status"}
    >
      <Icon aria-hidden="true" className="mt-0.5 shrink-0" size={17} />
      <span>{message}</span>
    </div>
  );
}

export function LoadingPanel({ label }: { label: string }) {
  return (
    <div
      className="flex min-h-40 items-center justify-center rounded-xl border border-slate-200 bg-white text-sm text-slate-500"
      role="status"
    >
      <LoaderCircle aria-hidden="true" className="mr-2 animate-spin motion-reduce:animate-none" size={18} />
      {label}
    </div>
  );
}

export function EmptyPanel({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-12 text-center">
      <h2 className="font-semibold text-slate-950">{title}</h2>
      <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-500">{description}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  busy,
  destructive = false,
  onConfirm,
  onClose,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  busy: boolean;
  destructive?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    return () => returnFocusRef.current?.focus();
  }, []);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !busy) onClose();
    if (event.key !== "Tab") return;
    if (event.shiftKey && document.activeElement === closeRef.current) {
      event.preventDefault();
      confirmRef.current?.focus();
    } else if (!event.shiftKey && document.activeElement === confirmRef.current) {
      event.preventDefault();
      closeRef.current?.focus();
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
      <section
        aria-describedby="controlled-dialog-description"
        aria-labelledby="controlled-dialog-title"
        aria-modal="true"
        className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"
        onKeyDown={handleKeyDown}
        role="dialog"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-slate-950" id="controlled-dialog-title">
              {title}
            </h2>
            <p className="mt-2 text-sm leading-6 text-slate-600" id="controlled-dialog-description">
              {description}
            </p>
          </div>
          <button
            aria-label="关闭确认框"
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
            disabled={busy}
            onClick={onClose}
            ref={closeRef}
            type="button"
          >
            <X aria-hidden="true" size={19} />
          </button>
        </div>
        <div className="mt-6 flex justify-end gap-3">
          <button className={secondaryButtonClassName} disabled={busy} onClick={onClose} ref={cancelRef} type="button">
            取消
          </button>
          <button
            className={destructive ? `${primaryButtonClassName} bg-red-700 hover:bg-red-800` : primaryButtonClassName}
            disabled={busy}
            onClick={onConfirm}
            ref={confirmRef}
            type="button"
          >
            {busy ? "处理中…" : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}

export function applicationStatusLabel(status: string) {
  return (
    {
      DRAFT: "待填写",
      SUBMITTED: "已提交",
      UNDER_REVIEW: "审核中",
      NEEDS_CORRECTION: "待补正",
      APPROVED: "已批准",
      REJECTED: "已拒绝",
    }[status] ?? status
  );
}

export function applicationStatusClassName(status: string) {
  if (status === "APPROVED") return "bg-emerald-50 text-emerald-700 ring-emerald-200";
  if (status === "REJECTED") return "bg-red-50 text-red-700 ring-red-200";
  if (status === "NEEDS_CORRECTION") return "bg-amber-50 text-amber-800 ring-amber-200";
  return "bg-blue-50 text-blue-700 ring-blue-200";
}
