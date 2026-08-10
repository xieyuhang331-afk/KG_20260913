import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, EyeOff, LockKeyhole, RefreshCw, XCircle } from "lucide-react";
import type { FormEvent } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { isApiError } from "@/shared/api/errors";
import { setCurrentUser } from "@/shared/auth/authStore";
import { clearAccessToken } from "@/shared/auth/tokenStorage";
import {
  approveIdentityReview,
  getIdentityReviewDetail,
  issueIdentityReviewStepUp,
  rejectIdentityReview,
} from "../实名认证审核接口";

type Decision = "approve" | "reject";

interface SensitiveIdentity {
  userId: number;
  submissionVersion: number;
  status: string;
  realName: string;
  identityValue: string;
  maskedValue: string;
  consentVersion: string;
  submittedAt: string;
}

interface DecisionAttempt {
  type: Decision;
  idempotencyKey: string;
  submissionVersion: number;
}

export function IdentityReviewDetailPage() {
  const { userId } = useParams();
  const numericUserId = Number(userId);

  if (!Number.isFinite(numericUserId) || numericUserId <= 0) {
    return <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700">无效的审核对象。</div>;
  }

  return <IdentityReviewDetailView key={numericUserId} userId={numericUserId} />;
}

export function IdentityReviewDetailView({ userId }: { userId: number }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const passwordInputRef = useRef<HTMLInputElement>(null);
  const stepUpAbortRef = useRef<AbortController | null>(null);
  const [sensitiveIdentity, setSensitiveIdentity] = useState<SensitiveIdentity | null>(null);
  const [sensitiveExpiresAt, setSensitiveExpiresAt] = useState<number | null>(null);
  const [stepUpPending, setStepUpPending] = useState(false);
  const [stepUpError, setStepUpError] = useState<string | null>(null);
  const [pageNotice, setPageNotice] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [decision, setDecision] = useState<DecisionAttempt | null>(null);
  const [decisionPending, setDecisionPending] = useState(false);
  const [decisionError, setDecisionError] = useState<string | null>(null);

  const clearSensitiveIdentity = useCallback(() => {
    setSensitiveIdentity(null);
    setSensitiveExpiresAt(null);
  }, []);

  useEffect(() => {
    return () => {
      stepUpAbortRef.current?.abort();
      stepUpAbortRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (sensitiveExpiresAt === null) return;
    const timeout = window.setTimeout(
      () => {
        clearSensitiveIdentity();
        setDecision(null);
        setPageNotice("临时查看已到期，敏感身份信息已自动隐藏。");
      },
      Math.max(0, sensitiveExpiresAt - Date.now()),
    );
    return () => window.clearTimeout(timeout);
  }, [clearSensitiveIdentity, sensitiveExpiresAt]);

  async function handleStepUp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const password = String(new FormData(form).get("password") ?? "");
    form.reset();
    if (!password || stepUpPending || decisionPending) return;

    stepUpAbortRef.current?.abort();
    const controller = new AbortController();
    stepUpAbortRef.current = controller;
    clearSensitiveIdentity();
    setDecision(null);
    setConflict(false);
    setStepUpError(null);
    setPageNotice(null);
    setStepUpPending(true);

    try {
      const stepUp = await issueIdentityReviewStepUp(userId, password, controller.signal);
      if (stepUp.token_type !== "identity_review_step_up" || stepUp.expires_in !== 120 || !stepUp.step_up_token) {
        throw new Error("Unexpected step-up contract");
      }

      const detail = await getIdentityReviewDetail(userId, stepUp.step_up_token, controller.signal);
      if (detail.user_id !== userId || detail.status !== "submitted") {
        setConflict(true);
        throw new Error("Identity review state conflict");
      }

      setSensitiveIdentity({
        userId: detail.user_id,
        submissionVersion: detail.submission_version,
        status: detail.status,
        realName: detail.real_name,
        identityValue: detail.id_card,
        maskedValue: detail.id_card_masked,
        consentVersion: detail.consent_version,
        submittedAt: detail.submitted_at,
      });
      setSensitiveExpiresAt(Date.now() + stepUp.expires_in * 1000);
      setPageNotice("二次认证成功。敏感身份信息将在120秒后自动隐藏。");
    } catch (error) {
      if (controller.signal.aborted) return;
      clearSensitiveIdentity();
      if (isApiError(error) && error.status === 409) {
        handleConflict();
        await queryClient.invalidateQueries({ queryKey: ["platform", "identity-review-queue"] });
      }
      setStepUpError(getSafeErrorMessage(error, "step-up"));
    } finally {
      if (stepUpAbortRef.current === controller) stepUpAbortRef.current = null;
      if (!controller.signal.aborted) setStepUpPending(false);
    }
  }

  function openDecision(type: Decision) {
    if (!sensitiveIdentity || stepUpPending || decisionPending) return;
    setDecision({
      type,
      idempotencyKey: `identity-review-${crypto.randomUUID()}`,
      submissionVersion: sensitiveIdentity.submissionVersion,
    });
    setDecisionError(null);
  }

  async function submitDecision() {
    if (!decision || !sensitiveIdentity || decisionPending) return;
    if (decision.submissionVersion !== sensitiveIdentity.submissionVersion) {
      handleConflict();
      return;
    }

    setDecisionPending(true);
    setDecisionError(null);
    try {
      if (decision.type === "approve") {
        await approveIdentityReview(userId, decision.submissionVersion, decision.idempotencyKey);
      } else {
        await rejectIdentityReview(userId, decision.submissionVersion, decision.idempotencyKey);
      }
      clearSensitiveIdentity();
      setDecision(null);
      await queryClient.invalidateQueries({ queryKey: ["platform", "identity-review-queue"] });
      navigate("/platform/identity-reviews", { replace: true });
    } catch (error) {
      if (isApiError(error) && error.status === 409) {
        handleConflict();
        await queryClient.invalidateQueries({ queryKey: ["platform", "identity-review-queue"] });
      } else if (isApiError(error) && error.status === 401) {
        clearSensitiveIdentity();
        clearAccessToken();
        setCurrentUser(null);
        navigate("/platform/login", { replace: true });
      } else {
        if (isApiError(error) && [403, 404, 422].includes(error.status)) {
          clearSensitiveIdentity();
          setDecision(null);
        }
        setDecisionError(getSafeErrorMessage(error, "decision"));
      }
    } finally {
      setDecisionPending(false);
    }
  }

  function handleConflict() {
    clearSensitiveIdentity();
    setDecision(null);
    setDecisionError(null);
    setConflict(true);
    setPageNotice("审核状态已变化，敏感信息已清除。请重新验证最新状态。");
  }

  function restartVerification() {
    setConflict(false);
    setStepUpError(null);
    passwordInputRef.current?.focus();
  }

  const interactionPending = stepUpPending || decisionPending;
  const canDecide = sensitiveIdentity !== null && !interactionPending;

  return (
    <div className="space-y-5">
      <Link className="inline-flex items-center gap-2 text-sm font-medium text-pine" to="/platform/identity-reviews">
        <ArrowLeft size={16} />
        返回待审核队列
      </Link>

      <header className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-pine">Identity Review Detail</p>
        <h1 className="mt-2 text-2xl font-semibold text-ink">实名认证审核详情</h1>
        <p className="mt-2 text-sm text-slate-500">审核对象：用户 #{userId}</p>
      </header>

      {conflict ? (
        <section className="flex flex-col gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-amber-800">审核状态已变化，需重新验证后读取最新详情。</p>
          <button
            className="inline-flex items-center justify-center gap-2 rounded-md border border-amber-300 bg-white px-3 py-2 text-sm font-medium text-amber-800"
            onClick={restartVerification}
            type="button"
          >
            <RefreshCw size={16} />
            重新验证最新状态
          </button>
        </section>
      ) : null}

      {pageNotice ? (
        <p aria-live="polite" className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
          {pageNotice}
        </p>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
        <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-start gap-3">
            <span className="rounded-lg bg-mint p-2.5 text-pine">
              <LockKeyhole size={20} />
            </span>
            <div>
              <h2 className="font-semibold text-ink">敏感身份信息默认隐藏</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                二次认证凭证仅用于本次请求，不进入URL、持久缓存、日志、埋点或错误报告。
              </p>
            </div>
          </div>

          {sensitiveIdentity ? (
            <div className="mt-6 rounded-lg border border-amber-200 bg-amber-50 p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.16em] text-amber-700">临时敏感视图</p>
                  <h3 className="mt-2 font-semibold text-ink">{sensitiveIdentity.realName}</h3>
                </div>
                <button
                  className="inline-flex items-center gap-2 rounded-md border border-amber-300 bg-white px-3 py-2 text-xs font-medium text-amber-800"
                  onClick={() => {
                    clearSensitiveIdentity();
                    setDecision(null);
                    setPageNotice("敏感身份信息已立即隐藏。");
                  }}
                  type="button"
                >
                  <EyeOff size={15} />
                  立即隐藏
                </button>
              </div>
              <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-2">
                <IdentityDefinition label="完整身份证号" value={sensitiveIdentity.identityValue} />
                <IdentityDefinition label="脱敏身份证号" value={sensitiveIdentity.maskedValue} />
                <IdentityDefinition label="提交版本" value={sensitiveIdentity.submissionVersion} />
                <IdentityDefinition label="同意版本" value={sensitiveIdentity.consentVersion} />
              </dl>
            </div>
          ) : (
            <form
              className="mt-6 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-5 py-8"
              onSubmit={handleStepUp}
            >
              <label className="block text-sm font-medium text-ink" htmlFor="identity-review-password">
                当前审核员密码
              </label>
              <p className="mt-2 text-xs leading-5 text-slate-500">
                验证成功后仅临时显示本次submitted申请，120秒后自动隐藏。
              </p>
              <div className="mt-4 flex flex-col gap-3 sm:flex-row">
                <input
                  autoComplete="current-password"
                  className="h-10 flex-1 rounded-md border border-slate-200 bg-white px-3 text-sm outline-none focus:border-pine"
                  disabled={interactionPending}
                  id="identity-review-password"
                  maxLength={256}
                  name="password"
                  ref={passwordInputRef}
                  required
                  type="password"
                />
                <button
                  className="h-10 rounded-md bg-pine px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={interactionPending}
                  type="submit"
                >
                  {stepUpPending ? "正在验证..." : "验证并临时查看"}
                </button>
              </div>
              {stepUpError ? <p className="mt-3 text-sm text-red-700">{stepUpError}</p> : null}
            </form>
          )}
        </section>

        <aside className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="font-semibold text-ink">审核操作</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            完成二次认证并核对submitted版本后方可决策；服务端生成权威决策时间。
          </p>
          <div className="mt-5 space-y-3">
            <button
              className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-pine px-4 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!canDecide}
              onClick={() => openDecision("approve")}
              type="button"
            >
              <CheckCircle2 size={17} />
              审核通过
            </button>
            <button
              className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-coral px-4 py-2.5 text-sm font-medium text-coral disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!canDecide}
              onClick={() => openDecision("reject")}
              type="button"
            >
              <XCircle size={17} />
              审核驳回
            </button>
          </div>
          {!sensitiveIdentity ? <p className="mt-3 text-xs text-slate-400">需先完成二次认证并查看最新申请。</p> : null}
          {!decision && decisionError ? <p className="mt-3 text-sm text-red-700">{decisionError}</p> : null}
        </aside>
      </div>

      {decision ? (
        <DecisionDialog
          decision={decision.type}
          error={decisionError}
          isSubmitting={decisionPending}
          onCancel={() => {
            if (decisionPending) return;
            setDecision(null);
            setDecisionError(null);
          }}
          onConfirm={() => void submitDecision()}
        />
      ) : null}
    </div>
  );
}

function IdentityDefinition({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 break-all font-medium text-ink">{value}</dd>
    </div>
  );
}

function DecisionDialog({
  decision,
  error,
  isSubmitting,
  onCancel,
  onConfirm,
}: {
  decision: Decision;
  error: string | null;
  isSubmitting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const approving = decision === "approve";
  const title = approving ? "确认审核通过" : "确认审核驳回";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-4" role="presentation">
      <section
        aria-label={title}
        aria-modal="true"
        className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl"
        role="dialog"
      >
        <h2 className="text-lg font-semibold text-ink">{title}</h2>
        <p className="mt-3 text-sm leading-6 text-slate-600">
          {approving
            ? "将以固定线下身份核验依据提交，通过后清除当前敏感视图。"
            : "将以固定线下核验失败原因提交，驳回后清除当前敏感视图。"}
        </p>
        {error ? <p className="mt-3 text-sm text-red-700">{error}</p> : null}
        <div className="mt-6 flex justify-end gap-3">
          <button
            className="rounded-md border border-slate-200 px-4 py-2 text-sm disabled:opacity-50"
            disabled={isSubmitting}
            onClick={onCancel}
            type="button"
          >
            取消
          </button>
          <button
            className="rounded-md bg-pine px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            disabled={isSubmitting}
            onClick={onConfirm}
            type="button"
          >
            {isSubmitting ? "正在提交..." : approving ? "确认通过" : "确认驳回"}
          </button>
        </div>
      </section>
    </div>
  );
}

function getSafeErrorMessage(error: unknown, stage: "step-up" | "decision") {
  if (!isApiError(error)) return "实名认证审核请求失败，请稍后重试。";
  if (stage === "step-up" && error.status === 401) return "密码错误、验证已过期或登录状态无效，请重新确认。";
  if (error.status === 403) return "当前账号没有执行实名认证审核的权限。";
  if (error.status === 404) return "待审核实名认证不存在或状态已变化。";
  if (error.status === 409) return "审核状态已变化，请刷新后重试。";
  if (error.status === 422) return "实名认证审核请求不符合当前合同，请刷新页面后重试。";
  if (error.status === 429) return "二次认证尝试过于频繁，请稍后重试。";
  if (error.status === 503) return "实名认证审核服务暂不可用，请稍后重试。";
  return "实名认证审核请求失败，请稍后重试。";
}
