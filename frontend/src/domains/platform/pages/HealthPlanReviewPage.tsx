import { ClipboardCheck, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  claimHealthPlanReview,
  decideHealthPlanReview,
  getHealthPlanReview,
  getSafeSlice6Error,
  listHealthPlanReviews,
  type PlanReviewDetailDTO,
  type PlanReviewListItemDTO,
  type ReviewDecisionInput,
  type ReviewReasonCode,
  type ReviewStatus,
} from "@/shared/api/slice6";
import { createIdempotencyKey, isUuidV7, toUuidV7 } from "@/shared/api/slice3";
import { Feedback, LoadingPanel, secondaryButtonClassName } from "@/domains/institution/受控入驻界面";

export function HealthPlanReviewPage() {
  const { reviewId = "" } = useParams();
  const [items, setItems] = useState<PlanReviewListItemDTO[]>([]);
  const [detail, setDetail] = useState<PlanReviewDetailDTO | null>(null);
  const [status, setStatus] = useState<ReviewStatus>("CLAIMED");
  const [cursor, setCursor] = useState<string>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [reasonCode, setReasonCode] = useState<ReviewReasonCode>("TEMPLATE_REAPPLY");
  const [rejectionReason, setRejectionReason] = useState<ReviewReasonCode>("MEDICAL_SAFETY_CONFLICT");
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
        const page = await listHealthPlanReviews({ status, cursor, limit: 20 });
        setItems(page.items);
        setNextCursor(page.next_cursor);
      }
      setFeedback("");
    } catch (error) {
      setFeedbackTone("error");
      setFeedback(getSafeSlice6Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [cursor, reviewId, status]);

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
            reason_codes: [
              decision === "APPROVED" ? "CONTENT_APPROVED" : decision === "REJECTED" ? rejectionReason : reasonCode,
            ],
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
          rejectionReason={rejectionReason}
          onClaim={claim}
          onDecision={decide}
          onReasonChange={setReasonCode}
          onRejectionReasonChange={setRejectionReason}
        />
      ) : (
        <ReviewList
          canGoBack={cursorHistory.length > 0}
          canGoNext={Boolean(nextCursor)}
          items={items}
          onNext={() => {
            if (!nextCursor) return;
            setCursorHistory((current) => [...current, cursor]);
            setCursor(nextCursor);
          }}
          onPrevious={() => {
            setCursorHistory((current) => {
              if (!current.length) return current;
              const previous = current[current.length - 1];
              setCursor(previous);
              return current.slice(0, -1);
            });
          }}
          status={status}
          onStatusChange={(value) => {
            setStatus(value);
            setCursor(undefined);
            setCursorHistory([]);
          }}
        />
      )}
    </main>
  );
}

function ReviewList({
  items,
  status,
  onStatusChange,
  canGoBack,
  canGoNext,
  onPrevious,
  onNext,
}: {
  items: PlanReviewListItemDTO[];
  status: ReviewStatus;
  onStatusChange: (value: ReviewStatus) => void;
  canGoBack: boolean;
  canGoNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
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
            onChange={(event) => onStatusChange(event.target.value as ReviewStatus)}
          >
            <option value="PENDING">待认领</option>
            <option value="CLAIMED">审核中</option>
            <option value="APPROVED">已批准</option>
            <option value="NEEDS_CORRECTION">需补正</option>
            <option value="REJECTED">已拒绝</option>
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
              <p className="mt-1 text-xs text-slate-500">方案版本 {item.plan_version_no}</p>
            </div>
            <span className="text-sm font-semibold text-teal-700">{reviewStatusLabel(item.status)}</span>
          </Link>
        ))
      ) : (
        <p className="px-5 py-10 text-center text-sm text-slate-500">当前筛选条件下没有审核任务。</p>
      )}
      <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">
        <button className={secondaryButtonClassName} disabled={!canGoBack} onClick={onPrevious} type="button">
          上一批
        </button>
        <button className={secondaryButtonClassName} disabled={!canGoNext} onClick={onNext} type="button">
          下一批
        </button>
      </div>
    </section>
  );
}

function ReviewDetail({
  detail,
  disabled,
  reasonCode,
  rejectionReason,
  onClaim,
  onDecision,
  onReasonChange,
  onRejectionReasonChange,
}: {
  detail: PlanReviewDetailDTO;
  disabled: boolean;
  reasonCode: ReviewReasonCode;
  rejectionReason: ReviewReasonCode;
  onClaim: () => void;
  onDecision: (decision: ReviewDecisionInput["decision"]) => void;
  onReasonChange: (value: ReviewReasonCode) => void;
  onRejectionReasonChange: (value: ReviewReasonCode) => void;
}) {
  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-5">
        <ReadOnlyCard title="客户健康档案摘要（只读）" items={displayCodes(detail.customer_summary_codes)} />
        <ReadOnlyCard title="评估摘要（只读）" items={displayCodes(detail.assessment_summary_codes)} />
        <ReadOnlyCard title="方案版本差异（只读）" items={displayCodes(detail.version_diff_codes)} />
        <ReadOnlyCard title="方案结构摘要（只读）" items={planSummaryItems(detail)} />
        <ReadOnlyCard title="审核历史（只读）" items={historyItems(detail)} />
      </div>
      <aside className="space-y-5">
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex items-center gap-2">
            <ClipboardCheck aria-hidden="true" className="text-teal-700" size={19} />
            <h2 className="font-semibold">审核操作</h2>
          </div>
          <p className="mt-3 text-sm text-slate-600">当前状态：{reviewStatusLabel(detail.status)}</p>
          {detail.status === "PENDING" ? (
            <button
              className="mt-4 w-full rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white"
              disabled={disabled}
              onClick={() => void onClaim()}
              type="button"
            >
              领取审核
            </button>
          ) : detail.status === "CLAIMED" ? (
            <div className="mt-4 space-y-3">
              <label className="block text-sm font-medium text-slate-700">
                结构化补正原因
                <select
                  aria-label="结构化补正原因"
                  className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2"
                  value={reasonCode}
                  onChange={(event) => onReasonChange(event.target.value as ReviewReasonCode)}
                >
                  <option value="TEMPLATE_REAPPLY">重新应用模板</option>
                  <option value="DATA_CONTEXT_RECHECK">复核评估数据上下文</option>
                </select>
              </label>
              <label className="block text-sm font-medium text-slate-700">
                结构化拒绝原因
                <select
                  aria-label="结构化拒绝原因"
                  className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2"
                  value={rejectionReason}
                  onChange={(event) => onRejectionReasonChange(event.target.value as ReviewReasonCode)}
                >
                  <option value="MEDICAL_SAFETY_CONFLICT">医学安全约束冲突</option>
                  <option value="TEMPLATE_SCOPE_UNSUITABLE">模板适用范围不匹配</option>
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
          ) : (
            <p className="mt-4 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">审核已完成，当前页面只读。</p>
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

interface DisplayItem {
  key: string;
  label: string;
}

function ReadOnlyCard({ title, items }: { title: string; items: DisplayItem[] }) {
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
      <h2 className="font-semibold">{title}</h2>
      {items.length ? (
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {items.map((item) => (
            <li className="rounded-lg bg-slate-50 px-3 py-2 text-sm" key={`${title}-${item.key}`}>
              {item.label}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-500">当前版本暂无可展示内容。</p>
      )}
    </article>
  );
}

function reviewStatusLabel(status: string) {
  return (
    (
      {
        PENDING: "待认领",
        CLAIMED: "审核中",
        APPROVED: "已批准",
        NEEDS_CORRECTION: "需补正",
        REJECTED: "已拒绝",
      } as Record<string, string>
    )[status] ?? "状态待核对"
  );
}

function summaryLabel(code: string) {
  const labels: Record<string, string> = {
    ASSESSMENT_INPUT_CURRENT: "当前健康档案输入有效",
    PROFILE_CONTEXT_INCLUDED: "客户健康背景已纳入",
    OVERALL_RISK_ATTENTION: "综合风险需关注",
    OVERALL_RISK_WITHIN_RANGE: "综合风险处于服务范围",
    INITIAL_VERSION: "首个方案版本，无前序差异",
    GOALS_CHANGED: "健康目标已更新",
    GOAL_BP: "保持血压管理目标",
    GOAL_GLUCOSE: "改善血糖管理目标",
    GOAL_LIPID: "改善血脂管理目标",
    GOAL_WEIGHT: "改善体重与腰围管理目标",
    WEIGHT_GOAL: "改善体重与腰围管理目标",
    STAGE_BASELINE: "基础管理阶段",
    FOUNDATION_STAGE: "基础管理阶段",
  };
  if (labels[code]) return labels[code];
  const riskSuffix = code.endsWith("_ATTENTION") ? "ATTENTION" : code.endsWith("_WITHIN_RANGE") ? "WITHIN_RANGE" : null;
  if (riskSuffix) {
    const moduleCode = code.slice(0, -(riskSuffix.length + 1));
    const module =
      (
        {
          BLOOD_PRESSURE_CARDIOVASCULAR: "血压与心血管",
          GLUCOSE_METABOLISM: "血糖代谢",
          LIPID_METABOLISM: "血脂代谢",
          WEIGHT_ABDOMINAL_OBESITY: "体重与腹型肥胖",
        } as Record<string, string>
      )[moduleCode] ?? "健康指标";
    return `${module}：${riskSuffix === "ATTENTION" ? "需关注" : "处于服务范围"}`;
  }
  return "已纳入结构化方案摘要";
}

function planSummaryItems(detail: PlanReviewDetailDTO) {
  return [
    { key: "template", label: templateLabel(detail.plan_summary.template_code) },
    { key: "template-version", label: `模板版本：${detail.plan_summary.template_version}` },
    { key: "plan-version", label: `方案版本：${detail.plan_version_no}` },
    ...displayCodes(detail.plan_summary.goals),
    ...displayCodes(detail.plan_summary.stages),
  ];
}

function templateLabel(code: string) {
  return (
    (
      {
        METABOLIC_FOUNDATION: "代谢健康基础方案",
        PHASE1_STANDARD: "一期标准健康管理方案",
        HTTP_STANDARD: "综合健康管理方案",
      } as Record<string, string>
    )[code] ?? "标准健康管理方案"
  );
}

function historyItems(detail: PlanReviewDetailDTO) {
  return detail.history.map((item) => ({
    key: `${item.action}-${item.plan_version_no}-${item.occurred_at}`,
    label: `${({ PLAN_GENERATED: "方案已生成", REVIEW_CLAIMED: "审核已领取", REVIEW_DECIDED: "审核已决定" } as Record<string, string>)[item.action] ?? "审核事实"} · 方案版本 ${item.plan_version_no}`,
  }));
}

function displayCodes(codes: string[]): DisplayItem[] {
  return codes.map((code) => ({ key: code, label: summaryLabel(code) }));
}
