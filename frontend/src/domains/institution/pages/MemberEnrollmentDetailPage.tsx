import { ArrowLeft, CheckCircle2, FileHeart, RefreshCw, ShieldCheck, UserRoundCheck } from "lucide-react";
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

export function MemberEnrollmentDetailPage() {
  const { enrollmentId = "" } = useParams();
  const [detail, setDetail] = useState<MemberEnrollmentDetail | null>(null);
  const [caseDetail, setCaseDetail] = useState<PreparingServiceCase | null>(null);
  const [therapists, setTherapists] = useState<TherapistProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ message: string; tone: "info" | "success" | "error" }>({
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
        listTherapists({ status: "APPROVED_ACTIVE", limit: 100 }).catch(() => ({ items: [], next_cursor: null })),
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
    if (!detail?.identity) {
      setFeedback({ message: "会员尚未提交实名资料，当前不能核验。", tone: "error" });
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
    if (!detail || !therapistId || scopeTags.length === 0) {
      setFeedback({ message: "请选择主健管师和至少一个服务领域。", tone: "error" });
      return;
    }
    if (!isUuidV7(therapistId)) {
      setFeedback({ message: "健管师标识格式无效，请刷新后重试。", tone: "error" });
      return;
    }
    await runMutation(
      () =>
        assignPrimaryTherapist(
          detail.enrollment_id,
          { therapist_id: therapistId, service_scope_tags: [...scopeTags].sort(), expected_version: detail.version },
          createIdempotencyKey(),
        ),
      "主健管师分配已创建，等待健管师接受。",
    );
  }

  async function cancelAssignment() {
    if (!detail?.assignment || !window.confirm("确认取消当前主健管师分配？")) return;
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

  if (loading) return <LoadingPanel label="正在加载会员服务准备详情…" />;

  return (
    <main className="mx-auto w-full max-w-[1280px] space-y-5">
      <div>
        <Link
          className="inline-flex items-center text-sm font-medium text-teal-700"
          to="/institution/member-enrollments"
        >
          <ArrowLeft aria-hidden="true" className="mr-1" size={16} />
          返回会员入组
        </Link>
      </div>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">会员服务 / 服务准备详情</p>
          <h1 className="mt-1 text-2xl font-semibold">会员入组详情</h1>
          <p className="mt-2 font-mono text-xs text-slate-500">{compactId(enrollmentId)}</p>
        </div>
        <button className={secondaryButtonClassName} disabled={busy} onClick={() => void load()} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      <Feedback message={feedback.message} tone={feedback.tone} />
      {detail ? (
        <>
          <section
            aria-label="会员旅程"
            className="grid gap-2 rounded-xl border border-slate-200 bg-white p-4 shadow-panel sm:grid-cols-5"
          >
            {journey(detail).map((step) => (
              <div
                className={`rounded-lg px-3 py-3 ${step.done ? "bg-teal-50 text-teal-800" : "bg-slate-50 text-slate-500"}`}
                key={step.label}
              >
                <div className="flex items-center gap-2 text-xs font-semibold">
                  {step.done ? (
                    <CheckCircle2 aria-hidden="true" size={15} />
                  ) : (
                    <span className="h-2 w-2 rounded-full bg-slate-300" />
                  )}
                  {step.label}
                </div>
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
                      <Field label="实名材料标识" value={compactId(detail.identity.current_revision_id)} />
                      <Field label="版本" value={String(detail.identity.version)} />
                    </dl>
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
                  </>
                ) : (
                  <p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                    会员尚未提交实名资料，机构不能提前核验。
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
                    <Field label="健管师" value={compactId(detail.assignment.therapist_id)} />
                    <div className="mt-3 flex items-center justify-between">
                      <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">
                        {assignmentStatusLabel(detail.assignment.status)}
                      </span>
                      <button
                        className={secondaryButtonClassName}
                        disabled={busy || detail.assignment.status === "CANCELLED"}
                        onClick={() => void cancelAssignment()}
                        type="button"
                      >
                        取消分配
                      </button>
                    </div>
                  </div>
                ) : (
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
                )}
              </article>
            </div>
            <aside className="space-y-5">
              <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
                <h2 className="font-semibold">入组摘要</h2>
                <dl className="mt-4 space-y-3 text-sm">
                  <Field label="模式" value={detail.mode === "SELF" ? "本人" : "代办老人"} />
                  <Field label="状态" value={enrollmentStatusLabel(detail.status)} />
                  <Field label="同意记录" value={`${detail.consents.length} 条`} />
                  <Field label="版本" value={String(detail.version)} />
                </dl>
              </article>
              <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
                <h2 className="font-semibold">服务案例</h2>
                {caseDetail ? (
                  <>
                    <dl className="mt-4 space-y-3 text-sm">
                      <Field label="状态" value="服务准备中" />
                      <Field label="案例标识" value={compactId(caseDetail.case_id)} />
                      <Field label="创建时间" value={formatTime(caseDetail.created_at)} />
                    </dl>
                    <Link
                      className={`${primaryButtonClassName} mt-5 w-full justify-center`}
                      to={`/institution/service-cases/${caseDetail.case_id}/health-record`}
                    >
                      <FileHeart aria-hidden="true" className="mr-2" size={17} />
                      查看健康档案与评估准备
                    </Link>
                  </>
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
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(date);
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
      PENDING: "等待健管师确认",
      ACCEPTED: "健管师已接受",
      DECLINED: "健管师已拒绝",
      CANCELLED: "分配已取消",
    }[status] ?? "状态待确认"
  );
}
function journey(detail: MemberEnrollmentDetail) {
  return [
    { label: "邀请已接受", done: Boolean(detail.accepted_at) },
    { label: "实名已提交", done: Boolean(detail.identity) },
    { label: "平台已终审", done: Boolean(detail.identity_verified_at) },
    { label: "主健管师已分配", done: Boolean(detail.assignment) },
    { label: "服务准备中", done: Boolean(detail.service_case_id) },
  ];
}
