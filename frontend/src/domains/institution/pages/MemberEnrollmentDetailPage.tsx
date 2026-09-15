import { ArrowLeft, CheckCircle2, Clock3, FileHeart, RefreshCw, ShieldCheck, UserRoundCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  assignPrimaryTherapist,
  cancelPrimaryAssignment,
  checkMemberIdentity,
  getMemberEnrollment,
  getPreparingServiceCase,
  listTherapists,
} from "../api";
import type { MemberEnrollmentDetail, PreparingServiceCase, ServiceScopeTag, TherapistProfile } from "../types";
import { createIdempotencyKey, getSafeApiError, isUuidV7 } from "@/shared/api/slice3";
import {
  Feedback,
  LoadingPanel,
  fieldClassName,
  primaryButtonClassName,
  secondaryButtonClassName,
} from "../受控入驻界面";

const scopeOptions: Array<{ value: ServiceScopeTag; label: string }> = [
  { value: "DYSLIPIDEMIA", label: "血脂异常" },
  { value: "GLUCOSE_METABOLISM", label: "糖代谢" },
  { value: "HYPERTENSION", label: "高血压" },
  { value: "OBESITY", label: "肥胖" },
];

const institutionReviewableIdentityStatuses = new Set(["SUBMITTED", "RESUBMITTED"]);
const knownIdentityStatuses = new Set([
  "SUBMITTED",
  "INSTITUTION_CHECKED",
  "PLATFORM_REVIEWING",
  "NEEDS_CORRECTION",
  "RESUBMITTED",
  "VERIFIED",
  "REJECTED",
]);
const requiredConsentTypes = [
  "USER_AGREEMENT",
  "PRIVACY_POLICY",
  "HEALTH_DATA_PROCESSING",
  "INSTITUTION_SERVICE",
  "NON_MEDICAL_RISK",
] as const;

export function MemberEnrollmentDetailPage() {
  const { enrollmentId = "" } = useParams();
  const [detail, setDetail] = useState<MemberEnrollmentDetail | null>(null);
  const [caseDetail, setCaseDetail] = useState<PreparingServiceCase | null>(null);
  const [therapists, setTherapists] = useState<TherapistProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{
    message: string;
    tone: "info" | "success" | "error";
  }>({
    message: "",
    tone: "info",
  });
  const [identityDecision, setIdentityDecision] = useState<"CHECKED" | "NEEDS_CORRECTION" | "REJECTED">("CHECKED");
  const [identityReason, setIdentityReason] = useState("IDENTITY_INFORMATION_INCONSISTENT");
  const [correctionFields, setCorrectionFields] = useState<Array<"real_name" | "id_number">>(["real_name"]);
  const [therapistId, setTherapistId] = useState("");
  const [scopeTags, setScopeTags] = useState<ServiceScopeTag[]>([]);

  const load = useCallback(async () => {
    if (!isUuidV7(enrollmentId)) {
      setFeedback({ message: "入组标识格式无效。", tone: "error" });
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const value = await getMemberEnrollment(enrollmentId);
      setDetail(value);
      const [therapistPage, serviceCase] = await Promise.all([
        listTherapists({ status: "APPROVED_ACTIVE", limit: 100 }).catch(() => ({
          items: [],
          next_cursor: null,
        })),
        value.service_case_id
          ? getPreparingServiceCase(value.service_case_id).catch(() => null)
          : Promise.resolve(null),
      ]);
      setTherapists(therapistPage.items);
      setCaseDetail(serviceCase);
      setFeedback({ message: "", tone: "info" });
    } catch (error) {
      setFeedback({ message: getSafeApiError(error).message, tone: "error" });
    } finally {
      setLoading(false);
    }
  }, [enrollmentId]);

  useEffect(() => void load(), [load]);

  async function runMutation(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    try {
      await action();
      await load();
      setFeedback({ message: success, tone: "success" });
    } catch (error) {
      const safe = getSafeApiError(error);
      if (safe.refreshRequired) await load();
      setFeedback({ message: safe.message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function submitIdentity(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !detail?.identity ||
      !institutionReviewableIdentityStatuses.has(detail.identity.status) ||
      hasUnsafeStateCombination(detail)
    ) {
      setFeedback({
        message: "当前实名状态不可由机构提交核验，请刷新后重试。",
        tone: "error",
      });
      return;
    }
    const checked = identityDecision === "CHECKED";
    const needsCorrection = identityDecision === "NEEDS_CORRECTION";
    const payload = {
      revision_id: detail.identity.current_revision_id,
      decision: identityDecision,
      reason_code: checked ? null : needsCorrection ? identityReason : "OFFLINE_CHECK_FAILED",
      correction_fields: needsCorrection ? correctionFields : [],
      attestation_code: checked
        ? detail.mode === "SELF"
          ? "OFFLINE_IDENTITY_CHECKED"
          : "PRINCIPAL_PRESENT_AND_AUTHORIZED_PROXY"
        : null,
      expected_version: detail.identity.version,
    } as const;
    await runMutation(
      () => checkMemberIdentity(detail.enrollment_id, payload, createIdempotencyKey()),
      "线下实名核验结果已提交。",
    );
  }

  async function submitAssignment(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (
      !detail ||
      hasUnsafeStateCombination(detail) ||
      detail.identity?.status !== "VERIFIED" ||
      !hasRequiredConsents(detail) ||
      detail.assignment ||
      !therapistId ||
      scopeTags.length === 0
    ) {
      setFeedback({
        message: "当前状态尚不能创建主健管师分配，请刷新并确认实名与服务同意状态。",
        tone: "error",
      });
      return;
    }
    if (!isUuidV7(therapistId)) {
      setFeedback({
        message: "健管师标识格式无效，请刷新后重试。",
        tone: "error",
      });
      return;
    }
    await runMutation(
      () =>
        assignPrimaryTherapist(
          detail.enrollment_id,
          {
            therapist_id: therapistId,
            service_scope_tags: [...scopeTags].sort(),
            expected_version: detail.version,
          },
          createIdempotencyKey(),
        ),
      "主健管师分配已创建，等待健管师接受。",
    );
  }

  async function cancelAssignment() {
    if (
      detail?.assignment?.status !== "PENDING_ACCEPTANCE" ||
      hasUnsafeStateCombination(detail) ||
      !window.confirm("确认取消当前主健管师分配？")
    )
      return;
    const assignment = detail.assignment;
    await runMutation(
      () =>
        cancelPrimaryAssignment(
          assignment.assignment_id,
          assignment.version,
          "INSTITUTION_CANCELLED",
          createIdempotencyKey(),
        ),
      "分配已取消。",
    );
  }

  if (loading) return <LoadingPanel label="正在加载客户服务接入详情…" />;

  const consentReady = detail ? hasRequiredConsents(detail) : false;
  const unsafeState = detail ? hasUnsafeStateCombination(detail) : false;
  const identityCanReview = Boolean(
    detail?.identity && institutionReviewableIdentityStatuses.has(detail.identity.status) && !unsafeState,
  );
  const assignmentCanCreate = Boolean(
    detail && !detail.assignment && detail.identity?.status === "VERIFIED" && consentReady && !unsafeState,
  );
  const assignedTherapist = detail?.assignment
    ? therapists.find((item) => item.therapist_id === detail.assignment?.therapist_id)
    : null;

  return (
    <main className="mx-auto w-full max-w-[1280px] space-y-5">
      <div>
        <Link
          className="inline-flex items-center text-sm font-medium text-teal-700"
          to="/institution/member-enrollments"
        >
          <ArrowLeft aria-hidden="true" className="mr-1" size={16} />
          返回服务客户列表
        </Link>
      </div>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">客户服务 / 接入详情</p>
          <h1 className="mt-1 text-2xl font-semibold">客户服务接入详情</h1>
        </div>
        <button className={secondaryButtonClassName} disabled={busy} onClick={() => void load()} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      <Feedback message={feedback.message} tone={feedback.tone} />
      {detail ? (
        <>
          {unsafeState ? (
            <section className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900" role="alert">
              状态信息暂不一致。为保护服务客户数据，相关操作已停用；请刷新后重试，仍未恢复请联系平台支持。
            </section>
          ) : null}
          <section
            aria-label="客户服务接入流程"
            className="grid gap-2 rounded-xl border border-slate-200 bg-white p-4 shadow-panel sm:grid-cols-3 xl:grid-cols-6"
          >
            {journey(detail, consentReady).map((step) => (
              <div
                aria-current={step.state === "current" ? "step" : undefined}
                className={`rounded-lg px-3 py-3 ${journeyStateClassName(step.state)}`}
                key={step.label}
              >
                <div className="flex items-center gap-2 text-xs font-semibold">
                  {step.state === "complete" ? (
                    <CheckCircle2 aria-hidden="true" size={15} />
                  ) : step.state === "current" ? (
                    <Clock3 aria-hidden="true" size={15} />
                  ) : (
                    <span className="h-2 w-2 rounded-full bg-slate-300" />
                  )}
                  {step.label}
                  <span className="sr-only">
                    {step.state === "complete" ? "已完成" : step.state === "current" ? "当前状态" : "待处理"}
                  </span>
                </div>
                <p className="mt-1 text-[11px] font-medium">
                  {step.state === "complete" ? "已完成" : step.state === "current" ? "当前状态" : "待处理"}
                </p>
              </div>
            ))}
          </section>
          <section className="grid gap-5 lg:grid-cols-[1.15fr_0.85fr]">
            <div className="space-y-5">
              <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
                <div className="flex items-center gap-2">
                  <ShieldCheck aria-hidden="true" className="text-teal-700" size={20} />
                  <h2 className="font-semibold">线下实名核验</h2>
                </div>
                {detail.identity ? (
                  <>
                    <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                      <Field label="脱敏证件" value={detail.identity.id_masked} />
                      <Field label="当前状态" value={identityStatusLabel(detail.identity.status)} />
                      <Field label="实名材料版本" value={`第 ${detail.identity.version} 版`} />
                    </dl>
                    {identityCanReview ? (
                      <form className="mt-5 space-y-3 border-t border-slate-100 pt-4" onSubmit={submitIdentity}>
                        <label className="block text-sm font-medium">
                          核验结果
                          <select
                            className={fieldClassName}
                            onChange={(event) => setIdentityDecision(event.target.value as typeof identityDecision)}
                            value={identityDecision}
                          >
                            <option value="CHECKED">核验通过</option>
                            <option value="NEEDS_CORRECTION">要求补正</option>
                            <option value="REJECTED">驳回</option>
                          </select>
                        </label>
                        {identityDecision === "NEEDS_CORRECTION" ? (
                          <>
                            <label className="block text-sm font-medium">
                              补正原因
                              <select
                                className={fieldClassName}
                                onChange={(event) => setIdentityReason(event.target.value)}
                                value={identityReason}
                              >
                                <option value="IDENTITY_INFORMATION_INCONSISTENT">身份信息不一致</option>
                                <option value="IDENTITY_DOCUMENT_INVALID">证件无效</option>
                                <option value="PROXY_EVIDENCE_INCOMPLETE">代理证明不完整</option>
                              </select>
                            </label>
                            <fieldset className="flex gap-4 text-sm">
                              <legend className="font-medium">补正字段</legend>
                              {(["real_name", "id_number"] as const).map((field) => (
                                <label key={field}>
                                  <input
                                    checked={correctionFields.includes(field)}
                                    onChange={(event) =>
                                      setCorrectionFields((values) =>
                                        event.target.checked
                                          ? [...new Set([...values, field])]
                                          : values.filter((value) => value !== field),
                                      )
                                    }
                                    type="checkbox"
                                  />{" "}
                                  {field === "real_name" ? "姓名" : "身份证号"}
                                </label>
                              ))}
                            </fieldset>
                          </>
                        ) : null}
                        <button className={primaryButtonClassName} disabled={busy} type="submit">
                          {busy ? "提交中…" : "确认并提交核验"}
                        </button>
                      </form>
                    ) : (
                      <p className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
                        当前实名状态由平台流程管理，机构仅可查看。
                      </p>
                    )}
                  </>
                ) : (
                  <p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                    服务客户尚未提交实名资料，机构不能提前核验。
                  </p>
                )}
              </article>
              <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
                <div className="flex items-center gap-2">
                  <UserRoundCheck aria-hidden="true" className="text-teal-700" size={20} />
                  <h2 className="font-semibold">主健管师分配</h2>
                </div>
                {detail.assignment ? (
                  <div className="mt-4">
                    <Field
                      label="健管师"
                      value={assignedTherapist?.display_name?.trim() || "已绑定健管师（名称待同步）"}
                    />
                    <div className="mt-3 flex items-center justify-between">
                      <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">
                        {assignmentStatusLabel(detail.assignment.status)}
                      </span>
                      {detail.assignment.status === "PENDING_ACCEPTANCE" && !unsafeState ? (
                        <button
                          className={secondaryButtonClassName}
                          disabled={busy}
                          onClick={() => void cancelAssignment()}
                          type="button"
                        >
                          取消分配
                        </button>
                      ) : null}
                    </div>
                  </div>
                ) : assignmentCanCreate ? (
                  <form className="mt-4 space-y-3" onSubmit={submitAssignment}>
                    <label className="block text-sm font-medium">
                      可用健管师
                      <select
                        className={fieldClassName}
                        onChange={(event) => setTherapistId(event.target.value)}
                        value={therapistId}
                      >
                        <option value="">请选择</option>
                        {therapists.map((item) => (
                          <option key={item.therapist_id} value={item.therapist_id}>
                            {item.display_name ?? compactId(item.therapist_id)} · {item.active_case_count}/
                            {item.capacity_limit}
                          </option>
                        ))}
                      </select>
                    </label>
                    <fieldset className="grid gap-2 sm:grid-cols-2">
                      <legend className="mb-2 text-sm font-medium">服务领域</legend>
                      {scopeOptions.map((item) => (
                        <label className="text-sm" key={item.value}>
                          <input
                            checked={scopeTags.includes(item.value)}
                            onChange={(event) =>
                              setScopeTags((values) =>
                                event.target.checked
                                  ? [...values, item.value]
                                  : values.filter((value) => value !== item.value),
                              )
                            }
                            type="checkbox"
                          />{" "}
                          {item.label}
                        </label>
                      ))}
                    </fieldset>
                    <button className={primaryButtonClassName} disabled={busy} type="submit">
                      创建分配
                    </button>
                  </form>
                ) : (
                  <p className="mt-4 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
                    完成平台实名终审和必要服务同意后，才可创建主健管师分配。
                  </p>
                )}
              </article>
            </div>
            <aside className="space-y-5">
              <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
                <h2 className="font-semibold">服务接入摘要</h2>
                <dl className="mt-4 space-y-3 text-sm">
                  <Field label="模式" value={detail.mode === "SELF" ? "本人" : "代办老人"} />
                  <Field label="状态" value={unsafeState ? "状态待确认" : enrollmentStatusLabel(detail.status)} />
                  <Field label="同意记录" value={`${detail.consents.length} 条`} />
                </dl>
              </article>
              <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
                <h2 className="font-semibold">服务案例</h2>
                {caseDetail && !unsafeState ? (
                  <>
                    <dl className="mt-4 space-y-3 text-sm">
                      <Field label="状态" value="服务准备中" />
                      <Field label="创建时间" value={formatTime(caseDetail.created_at)} />
                    </dl>
                    <Link
                      className={`${primaryButtonClassName} mt-5 w-full justify-center`}
                      to={`/institution/service-cases/${caseDetail.case_id}/health-record`}
                    >
                      <FileHeart aria-hidden="true" className="mr-2" size={17} />
                      查看客户健康档案与评估准备
                    </Link>
                  </>
                ) : detail.service_case_id ? (
                  <p className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                    服务案例状态暂不可确认，请刷新后重试。
                  </p>
                ) : (
                  <p className="mt-3 text-sm text-slate-500">主健管师接受分配后，系统才会创建服务准备中案例。</p>
                )}
              </article>
            </aside>
          </section>
        </>
      ) : (
        <section className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-800">
          详情不可用。请返回列表后重试。
        </section>
      )}
    </main>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="mt-1 break-all font-medium text-slate-800">{value}</dd>
    </div>
  );
}
function compactId(value: string) {
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}
function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待确认"
    : new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "short",
        timeStyle: "short",
      }).format(date);
}
function identityStatusLabel(status: string) {
  return (
    {
      SUBMITTED: "实名材料已提交，待机构核验",
      INSTITUTION_CHECKED: "机构核验通过，待平台终审",
      PLATFORM_REVIEWING: "平台审核中",
      NEEDS_CORRECTION: "需要补正实名材料",
      RESUBMITTED: "补正材料已重新提交",
      VERIFIED: "实名认证已通过",
      REJECTED: "实名认证未通过",
    }[status] ?? "状态待确认"
  );
}
function enrollmentStatusLabel(status: string) {
  return (
    {
      ACCEPTED: "已接受邀请",
      IDENTITY_SUBMITTED: "实名材料待机构核验",
      INSTITUTION_CHECKED: "机构核验通过，待平台终审",
      PLATFORM_REVIEWING: "平台审核中",
      NEEDS_CORRECTION: "实名材料待补正",
      RESUBMITTED: "补正材料待复核",
      IDENTITY_VERIFIED: "实名认证已通过",
      CONSENT_PENDING: "待确认服务同意",
      THERAPIST_PENDING: "待分配主健管师",
      CASE_CREATED: "服务准备中",
      REJECTED: "入组未通过",
      REVOKED: "入组已撤销",
      EXPIRED: "邀请已过期",
    }[status] ?? "状态待确认"
  );
}
function assignmentStatusLabel(status: string) {
  return (
    {
      PENDING_ACCEPTANCE: "等待健管师确认",
      ACCEPTED: "健管师已接受",
      DECLINED: "健管师已拒绝",
      CANCELLED: "分配已取消",
    }[status] ?? "状态待确认"
  );
}

type JourneyState = "complete" | "current" | "pending";

function journey(detail: MemberEnrollmentDetail, consentReady: boolean): Array<{ label: string; state: JourneyState }> {
  const identityVerified = detail.identity?.status === "VERIFIED";
  const assignmentAccepted = detail.assignment?.status === "ACCEPTED";
  const assignmentPending = detail.assignment?.status === "PENDING_ACCEPTANCE";
  return [
    {
      label: "邀请已接受",
      state: detail.accepted_at ? "complete" : "current",
    },
    {
      label: "实名已提交",
      state: detail.identity ? "complete" : detail.accepted_at ? "current" : "pending",
    },
    {
      label: "平台已终审",
      state: identityVerified ? "complete" : detail.identity ? "current" : "pending",
    },
    {
      label: "服务同意已确认",
      state: consentReady ? "complete" : identityVerified ? "current" : "pending",
    },
    {
      label: assignmentAccepted ? "主健管师已接受" : assignmentPending ? "等待健管师接受" : "主健管师待确认",
      state: assignmentAccepted ? "complete" : assignmentPending || consentReady ? "current" : "pending",
    },
    {
      label: hasUnsafeStateCombination(detail)
        ? "服务状态待确认"
        : detail.service_case_id
          ? "服务准备中"
          : "服务案例待创建",
      state: hasUnsafeStateCombination(detail)
        ? "pending"
        : detail.service_case_id
          ? "current"
          : assignmentAccepted
            ? "current"
            : "pending",
    },
  ];
}

function journeyStateClassName(state: JourneyState) {
  if (state === "complete") return "bg-teal-50 text-teal-800";
  if (state === "current") return "bg-blue-50 text-blue-800";
  return "bg-slate-50 text-slate-500";
}

function hasRequiredConsents(detail: MemberEnrollmentDetail) {
  const required = [...requiredConsentTypes, ...(detail.mode === "PROXY_ELDER" ? ["PROXY_AUTHORIZATION"] : [])];
  return required.every((documentType) =>
    detail.consents.some(
      (consent) =>
        consent.document_type === documentType &&
        consent.choice === "ACCEPTED" &&
        consent.status === "ACCEPTED" &&
        Boolean(consent.accepted_at) &&
        !consent.withdrawn_at,
    ),
  );
}

function hasUnsafeStateCombination(detail: MemberEnrollmentDetail) {
  if (detail.identity && !knownIdentityStatuses.has(detail.identity.status)) {
    return true;
  }
  if (!detail.service_case_id) return false;
  return (
    detail.identity?.status !== "VERIFIED" || !hasRequiredConsents(detail) || detail.assignment?.status !== "ACCEPTED"
  );
}
