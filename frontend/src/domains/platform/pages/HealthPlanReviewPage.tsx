import { ClipboardCheck, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  claimHealthPlanReview,
  decideHealthPlanReview,
  getHealthPlanReview,
  getSafeSlice6Error,
  listHealthPlanReviews,
  type HealthPlanReviewDetail,
  type HealthPlanReviewSummary,
  type ReviewDecisionInput,
} from "@/shared/api/slice6";
import { createIdempotencyKey, isUuidV7, toUuidV7 } from "@/shared/api/slice3";
import { Feedback, LoadingPanel, secondaryButtonClassName } from "@/domains/institution/受控入驻界面";

export function HealthPlanReviewPage() {
  const { reviewId = "" } = useParams();
  const [items, setItems] = useState<HealthPlanReviewSummary[]>([]);
  const [detail, setDetail] = useState<HealthPlanReviewDetail | null>(null);
  const [status, setStatus] = useState("IN_REVIEW");
  const [reasonCode, setReasonCode] = useState("GOAL_REQUIRES_CORRECTION");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [feedbackTone, setFeedbackTone] = useState<"success" | "error">("error");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (reviewId) {
        if (!isUuidV7(reviewId)) throw new Error("UUID_V7_REQUIRED");
        setDetail(await getHealthPlanReview(toUuidV7(reviewId)));
      } else {
        setItems((await listHealthPlanReviews({ status, limit: 20 })).items);
      }
      setFeedback("");
    } catch (error) {
      setFeedbackTone("error");
      setFeedback(getSafeSlice6Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [reviewId, status]);

  useEffect(() => {
    void load();
  }, [load]);

  async function claim() {
    if (!detail || submitting) return;
    setSubmitting(true);
    try {
      setDetail(await claimHealthPlanReview(detail.review_id, detail.version, createIdempotencyKey()));
      setFeedbackTone("success");
      setFeedback("审核任务已领取。");
    } catch (error) {
      const safe = getSafeSlice6Error(error);
      if (safe.refreshRequired) await load();
      setFeedbackTone("error");
      setFeedback(safe.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function decide(decision: ReviewDecisionInput["decision"]) {
    if (!detail || submitting) return;
    if (
      !window.confirm(
        decision === "APPROVED"
          ? "确认批准当前结构化方案版本？"
          : decision === "REJECTED"
            ? "确认拒绝当前方案？"
            : "确认要求系统按结构化原因生成补正版？",
      )
    )
      return;
    setSubmitting(true);
    try {
      setDetail(
        await decideHealthPlanReview(
          detail.review_id,
          {
            decision,
            reason_codes: decision === "APPROVED" ? [] : [reasonCode],
            expected_version: detail.version,
          },
          createIdempotencyKey(),
        ),
      );
      setFeedbackTone("success");
      setFeedback("审核决定已提交。");
    } catch (error) {
      const safe = getSafeSlice6Error(error);
      if (safe.refreshRequired) await load();
      setFeedbackTone("error");
      setFeedback(safe.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <LoadingPanel label="正在加载健康方案审核…" />;

  return (
    <main className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">医学专家工作台</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">{detail ? "健康方案审核详情" : "健康方案审核"}</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
            基于客户健康档案与确定性评估摘要进行只读复核，并提交结构化审核决定。
          </p>
        </div>
        <button className={secondaryButtonClassName} onClick={() => void load()} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      <Feedback message={feedback} tone={feedbackTone} />
      {detail ? (
        <ReviewDetail
          detail={detail}
          disabled={submitting}
          reasonCode={reasonCode}
          onClaim={claim}
          onDecision={decide}
          onReasonChange={setReasonCode}
        />
      ) : (
        <ReviewList items={items} status={status} onStatusChange={setStatus} />
      )}
    </main>
  );
}

function ReviewList({
  items,
  status,
  onStatusChange,
}: {
  items: HealthPlanReviewSummary[];
  status: string;
  onStatusChange: (value: string) => void;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-panel">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <h2 className="font-semibold">审核队列</h2>
        <label className="text-sm text-slate-600">
          状态{" "}
          <select
            className="ml-2 rounded-lg border border-slate-200 px-3 py-2"
            value={status}
            onChange={(event) => onStatusChange(event.target.value)}
          >
            <option value="PENDING_CLAIM">待认领</option>
            <option value="IN_REVIEW">审核中</option>
            <option value="COMPLETED">已完成</option>
          </select>
        </label>
      </div>
      {items.length ? (
        items.map((item) => (
          <Link
            className="grid gap-2 border-b border-slate-100 px-5 py-4 last:border-0 sm:grid-cols-[1fr_auto]"
            key={item.review_id}
            to={`/platform/health-plan-reviews/${item.review_id}`}
          >
            <div>
              <p className="font-medium">结构化健康管理方案</p>
              <p className="mt-1 text-xs text-slate-500">方案版本 {item.plan_version}</p>
            </div>
            <span className="text-sm font-semibold text-teal-700">{reviewStatusLabel(item.status)}</span>
          </Link>
        ))
      ) : (
        <p className="px-5 py-10 text-center text-sm text-slate-500">当前筛选条件下没有审核任务。</p>
      )}
    </section>
  );
}

function ReviewDetail({
  detail,
  disabled,
  reasonCode,
  onClaim,
  onDecision,
  onReasonChange,
}: {
  detail: HealthPlanReviewDetail;
  disabled: boolean;
  reasonCode: string;
  onClaim: () => void;
  onDecision: (decision: ReviewDecisionInput["decision"]) => void;
  onReasonChange: (value: string) => void;
}) {
  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-5">
        <ReadOnlyCard title="客户健康档案摘要（只读）" items={detail.customer_summary_codes.map(summaryLabel)} />
        <ReadOnlyCard title="评估摘要（只读）" items={detail.assessment_summary_codes.map(summaryLabel)} />
        <ReadOnlyCard title="方案版本差异（只读）" items={detail.version_diff_codes.map(summaryLabel)} />
      </div>
      <aside className="space-y-5">
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex items-center gap-2">
            <ClipboardCheck aria-hidden="true" className="text-teal-700" size={19} />
            <h2 className="font-semibold">审核操作</h2>
          </div>
          <p className="mt-3 text-sm text-slate-600">当前状态：{reviewStatusLabel(detail.status)}</p>
          {!detail.claimed ? (
            <button
              className="mt-4 w-full rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white"
              disabled={disabled}
              onClick={() => void onClaim()}
              type="button"
            >
              领取审核
            </button>
          ) : (
            <div className="mt-4 space-y-3">
              <label className="block text-sm font-medium text-slate-700">
                结构化补正原因
                <select
                  aria-label="结构化补正原因"
                  className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2"
                  value={reasonCode}
                  onChange={(event) => onReasonChange(event.target.value)}
                >
                  <option value="GOAL_REQUIRES_CORRECTION">健康目标需补正</option>
                  <option value="STAGE_REQUIRES_CORRECTION">执行阶段需补正</option>
                  <option value="SAFETY_CONSTRAINT_CONFLICT">安全约束冲突</option>
                </select>
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  className="rounded-lg bg-primary-600 px-3 py-2 text-sm font-semibold text-white"
                  disabled={disabled}
                  onClick={() => void onDecision("APPROVED")}
                  type="button"
                >
                  批准
                </button>
                <button
                  className="rounded-lg border border-amber-200 px-3 py-2 text-sm font-semibold text-amber-800"
                  disabled={disabled}
                  onClick={() => void onDecision("NEEDS_CORRECTION")}
                  type="button"
                >
                  要求补正
                </button>
              </div>
              <button
                className="w-full rounded-lg border border-rose-200 px-3 py-2 text-sm font-semibold text-rose-700"
                disabled={disabled}
                onClick={() => void onDecision("REJECTED")}
                type="button"
              >
                拒绝
              </button>
            </div>
          )}
        </article>
        <article className="rounded-xl border border-teal-200 bg-teal-50 p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" size={19} />
            <h2 className="font-semibold">医学锁定边界</h2>
          </div>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            专家提交批准、拒绝或结构化补正码，不直接改写方案正文。
          </p>
        </article>
      </aside>
    </section>
  );
}

function ReadOnlyCard({ title, items }: { title: string; items: string[] }) {
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
      <h2 className="font-semibold">{title}</h2>
      <ul className="mt-3 grid gap-2 sm:grid-cols-2">
        {items.map((item) => (
          <li className="rounded-lg bg-slate-50 px-3 py-2 text-sm" key={item}>
            {item}
          </li>
        ))}
      </ul>
    </article>
  );
}

function reviewStatusLabel(status: string) {
  return (
    ({ PENDING_CLAIM: "待认领", IN_REVIEW: "审核中", COMPLETED: "已完成" } as Record<string, string>)[status] ??
    "状态待核对"
  );
}

function summaryLabel(code: string) {
  return (
    (
      {
        PROFILE_COMPLETE: "健康档案已完整",
        ASSESSMENT_COMPLETED: "健康评估已完成",
        METABOLIC_RISK_MEDIUM: "评估摘要：代谢风险需持续管理",
        INITIAL_VERSION: "首个方案版本",
      } as Record<string, string>
    )[code] ?? "结构化摘要"
  );
}
