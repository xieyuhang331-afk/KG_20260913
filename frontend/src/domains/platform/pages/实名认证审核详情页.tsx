import { ArrowLeft, Eye, EyeOff, RefreshCw, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  accessMemberIdentityPii,
  claimMemberIdentityReview,
  decideMemberIdentityReview,
  getMemberIdentityReview,
} from "../实名认证审核接口";
import type { MemberIdentityPii, MemberIdentityReviewDetail } from "../实名认证审核类型";
import { createIdempotencyKey, getSafeApiError, isUuidV7 } from "@/shared/api/slice3";

const sensitiveLifetimeMs = 60_000;

export function IdentityReviewDetailPage() {
  const { reviewId = "" } = useParams();
  const [detail, setDetail] = useState<MemberIdentityReviewDetail | null>(null);
  const [pii, setPii] = useState<MemberIdentityPii | null>(null);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [decision, setDecision] = useState<"APPROVED" | "NEEDS_CORRECTION" | "REJECTED">("APPROVED");
  const [reason, setReason] = useState("IDENTITY_INFORMATION_INCONSISTENT");
  const [correctionFields, setCorrectionFields] = useState<Array<"real_name" | "id_number">>(["real_name"]);
  const detailController = useRef<AbortController | null>(null);
  const piiController = useRef<AbortController | null>(null);

  const clearSensitive = useCallback(() => {
    piiController.current?.abort();
    piiController.current = null;
    setPii(null);
    setPassword("");
  }, []);

  const load = useCallback(async () => {
    clearSensitive();
    if (!isUuidV7(reviewId)) {
      setError("审核标识格式无效。");
      setLoading(false);
      return;
    }
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    setLoading(true);
    try {
      const value = await getMemberIdentityReview(reviewId, controller.signal);
      if (!controller.signal.aborted && detailController.current === controller) {
        setDetail(value);
        setError("");
      }
    } catch (reasonValue) {
      if (!controller.signal.aborted) setError(getSafeApiError(reasonValue).message);
    } finally {
      if (detailController.current === controller) {
        detailController.current = null;
        setLoading(false);
      }
    }
  }, [clearSensitive, reviewId]);

  useEffect(() => {
    void load();
    return () => {
      detailController.current?.abort();
      clearSensitive();
    };
  }, [load, clearSensitive]);

  useEffect(() => {
    if (!pii) return;
    const timer = window.setTimeout(clearSensitive, sensitiveLifetimeMs);
    return () => window.clearTimeout(timer);
  }, [pii, clearSensitive]);

  async function runMutation(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    setSuccess("");
    try {
      await action();
      clearSensitive();
      await load();
      setSuccess(message);
    } catch (reasonValue) {
      const safe = getSafeApiError(reasonValue);
      if (safe.status === 403 || safe.refreshRequired) clearSensitive();
      if (safe.refreshRequired) await load();
      setError(safe.message);
    } finally {
      setBusy(false);
    }
  }

  async function claim() {
    if (!detail || !window.confirm("确认领取该审核项？")) return;
    await runMutation(
      () => claimMemberIdentityReview(detail.review_id, detail.version, createIdempotencyKey()),
      "审核项已领取。",
    );
  }

  async function reveal(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const currentPassword = password;
    setPassword("");
    setBusy(true);
    setError("");
    clearSensitive();
    const controller = new AbortController();
    piiController.current = controller;
    try {
      const value = await accessMemberIdentityPii(
        detail.review_id,
        currentPassword,
        "PLATFORM_IDENTITY_REVIEW",
        createIdempotencyKey(),
        controller.signal,
      );
      if (!controller.signal.aborted && piiController.current === controller) setPii(value);
    } catch (reasonValue) {
      if (!controller.signal.aborted) {
        const safe = getSafeApiError(reasonValue);
        clearSensitive();
        if (safe.refreshRequired) await load();
        setError(safe.message);
      }
    } finally {
      if (piiController.current === controller) piiController.current = null;
      setBusy(false);
    }
  }

  async function submitDecision(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || !window.confirm("确认提交该审核决定？提交后不会自动重放。")) return;
    const needsCorrection = decision === "NEEDS_CORRECTION";
    const payload = {
      revision_id: detail.current_revision_id,
      decision,
      reason_code: decision === "APPROVED" ? null : needsCorrection ? reason : "IDENTITY_DOCUMENT_INVALID",
      correction_fields: needsCorrection ? correctionFields : [],
      represented_elder_eligible: decision === "APPROVED" && detail.mode === "PROXY_ELDER" ? true : null,
      expected_version: detail.version,
    };
    await runMutation(
      () => decideMemberIdentityReview(detail.review_id, payload, createIdempotencyKey()),
      "审核决定已提交。",
    );
  }

  return (
    <main className="mx-auto w-full max-w-[1280px] space-y-5">
      <Link
        className="inline-flex items-center text-sm font-semibold text-teal-700"
        onClick={clearSensitive}
        to="/platform/identity-reviews"
      >
        <ArrowLeft aria-hidden="true" className="mr-1" size={16} />
        返回审核队列
      </Link>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">会员治理 / 身份终审详情</p>
          <h1 className="mt-1 text-2xl font-semibold">实名认证审核详情</h1>
          <p className="mt-2 font-mono text-xs text-slate-500">{reviewId}</p>
        </div>
        <button
          className="inline-flex items-center rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold"
          disabled={busy || loading}
          onClick={() => void load()}
          type="button"
        >
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      {error ? (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800" role="alert">
          {error}
        </section>
      ) : null}
      {success ? (
        <section
          className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800"
          role="status"
        >
          {success}
        </section>
      ) : null}
      {loading ? (
        <div className="rounded-xl bg-white p-8 text-center text-sm text-slate-500">正在加载审核详情…</div>
      ) : detail ? (
        <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
          <div className="space-y-5">
            <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold">脱敏审核资料</h2>
                <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">
                  {reviewStatusLabel(detail.status)}
                </span>
              </div>
              <dl className="mt-5 grid gap-4 sm:grid-cols-2">
                <Field label="脱敏证件" value={detail.id_masked} />
                <Field label="入组模式" value={detail.mode === "SELF" ? "本人" : "代办老人"} />
                <Field label="当前实名材料" value={compactId(detail.current_revision_id)} />
                <Field label="材料版本" value={`第 ${detail.current_revision_no} 版`} />
                <Field label="机构核验声明" value={attestationLabel(detail.institution_attestation)} />
                <Field label="代理见证状态" value={detail.proxy_witness_status ?? "不适用"} />
              </dl>
              <button
                className="mt-5 rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold"
                disabled={busy}
                onClick={() => void claim()}
                type="button"
              >
                领取审核
              </button>
            </section>
            <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
              <div className="flex items-center gap-2">
                <ShieldAlert aria-hidden="true" className="text-amber-600" size={20} />
                <h2 className="font-semibold">一次性查看完整身份信息</h2>
              </div>
              <p className="mt-2 text-sm text-slate-500">需当前审核员密码重认证；成功后仅在本组件内存显示 60 秒。</p>
              {pii ? (
                <div className="mt-4 rounded-xl bg-slate-950 p-5 text-white">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold">敏感信息正在显示</span>
                    <button
                      className="inline-flex items-center text-xs text-slate-200"
                      onClick={clearSensitive}
                      type="button"
                    >
                      <EyeOff aria-hidden="true" className="mr-1" size={14} />
                      立即隐藏
                    </button>
                  </div>
                  <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                    <Field label="姓名" value={pii.real_name} dark />
                    <Field label="身份证号" value={pii.id_number} dark />
                    <Field label="出生日期" value={pii.birth_date} dark />
                  </dl>
                </div>
              ) : (
                <form className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={reveal}>
                  <label className="min-w-0 flex-1 text-sm font-medium">
                    当前密码
                    <input
                      aria-label="当前审核员密码"
                      autoComplete="current-password"
                      className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
                      onChange={(event) => setPassword(event.target.value)}
                      type="password"
                      value={password}
                    />
                  </label>
                  <button
                    className="inline-flex items-center justify-center rounded-lg bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                    disabled={busy || !password}
                    type="submit"
                  >
                    <Eye aria-hidden="true" className="mr-2" size={16} />
                    临时查看
                  </button>
                </form>
              )}
            </section>
          </div>
          <aside>
            <form
              className="sticky top-24 space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-panel"
              onSubmit={submitDecision}
            >
              <h2 className="font-semibold">审核决定</h2>
              <label className="block text-sm font-medium">
                决定
                <select
                  className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
                  onChange={(event) => setDecision(event.target.value as typeof decision)}
                  value={decision}
                >
                  <option value="APPROVED">通过</option>
                  <option value="NEEDS_CORRECTION">要求补正</option>
                  <option value="REJECTED">驳回</option>
                </select>
              </label>
              {decision === "NEEDS_CORRECTION" ? (
                <>
                  <label className="block text-sm font-medium">
                    原因
                    <select
                      className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
                      onChange={(event) => setReason(event.target.value)}
                      value={reason}
                    >
                      <option value="IDENTITY_INFORMATION_INCONSISTENT">身份信息不一致</option>
                      <option value="IDENTITY_DOCUMENT_INVALID">证件无效</option>
                      <option value="PROXY_EVIDENCE_INCOMPLETE">代理证明不完整</option>
                    </select>
                  </label>
                  <fieldset className="space-y-2 text-sm">
                    <legend className="font-medium">补正字段</legend>
                    {(["real_name", "id_number"] as const).map((field) => (
                      <label className="block" key={field}>
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
              <p className="rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-800">
                提交以当前材料版本为准；数据冲突时只刷新最新状态，不重复提交决定。
              </p>
              <button
                className="w-full rounded-lg bg-teal-700 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
                disabled={busy}
                type="submit"
              >
                {busy ? "处理中…" : "确认提交决定"}
              </button>
            </form>
          </aside>
        </div>
      ) : (
        <section className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-800">
          详情不可用，请返回队列后重试。
        </section>
      )}
    </main>
  );
}

function Field({ label, value, dark = false }: { label: string; value: string; dark?: boolean }) {
  return (
    <div>
      <dt className={`text-xs ${dark ? "text-slate-400" : "text-slate-400"}`}>{label}</dt>
      <dd className={`mt-1 break-all font-medium ${dark ? "text-white" : "text-slate-800"}`}>{value}</dd>
    </div>
  );
}
function compactId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}
function reviewStatusLabel(status: string) {
  return (
    {
      INSTITUTION_CHECKED: "机构核验通过，待平台终审",
      CLAIMED: "审核员已领取",
      PLATFORM_REVIEWING: "平台审核中",
      NEEDS_CORRECTION: "待会员补正",
      VERIFIED: "审核已通过",
      APPROVED: "审核已通过",
      REJECTED: "审核未通过",
    }[status] ?? "状态待确认"
  );
}
function attestationLabel(value: string | null) {
  if (value === "OFFLINE_IDENTITY_CHECKED") return "机构已完成线下实名核验";
  if (value === "PRINCIPAL_PRESENT_AND_AUTHORIZED_PROXY") return "本人在场并已授权代办";
  return "未提供";
}
