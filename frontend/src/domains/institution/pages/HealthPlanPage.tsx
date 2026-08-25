import { Clock3, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  createPlanGeneration,
  getInstitutionPlan,
  getPlanGeneration,
  getPlanGenerationEligibility,
  getSafeSlice6Error,
  listInstitutionPlans,
  type HealthPlanDetail,
  type HealthPlanSummary,
  type PlanGenerationEligibility,
  type PlanGenerationRequest,
} from "@/shared/api/slice6";
import { createIdempotencyKey, isUuidV7, toUuidV7 } from "@/shared/api/slice3";
import { Feedback, LoadingPanel, secondaryButtonClassName } from "../受控入驻界面";

export function PlanEligibilityPanel({ caseId, autoLoad = true }: { caseId: string; autoLoad?: boolean }) {
  const [eligibility, setEligibility] = useState<PlanGenerationEligibility | null>(null);
  const [generation, setGeneration] = useState<PlanGenerationRequest | null>(null);
  const [loading, setLoading] = useState(autoLoad);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [feedbackTone, setFeedbackTone] = useState<"success" | "error">("error");
  const submissionLock = useRef(false);
  const submissionCompleted = useRef(false);
  const pendingKey = useRef<string | null>(null);

  const load = useCallback(async () => {
    if (!isUuidV7(caseId)) {
      setFeedback("服务案例标识无效，无法核验方案生成资格。");
      return;
    }
    setLoading(true);
    try {
      setEligibility(await getPlanGenerationEligibility(toUuidV7(caseId)));
      setFeedback("");
    } catch (error) {
      setFeedbackTone("error");
      setFeedback(getSafeSlice6Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => {
    if (autoLoad) void load();
  }, [autoLoad, load]);

  async function generate() {
    if (!eligibility?.eligible || submissionLock.current || submissionCompleted.current) return;
    if (!window.confirm("确认按当前评估与已发布模板生成健康管理方案？")) return;
    submissionLock.current = true;
    setSubmitting(true);
    pendingKey.current = createIdempotencyKey();
    try {
      const result = await createPlanGeneration(
        toUuidV7(caseId),
        eligibility.expected_service_case_version,
        pendingKey.current,
      );
      setGeneration(result);
      submissionCompleted.current = true;
      setFeedbackTone("success");
      setFeedback("方案生成请求已受理，请通过处理进度确认后续状态。");
    } catch (error) {
      const safe = getSafeSlice6Error(error);
      if (safe.status === 409) await load();
      setFeedbackTone("error");
      setFeedback(safe.message);
    } finally {
      setSubmitting(false);
      submissionLock.current = false;
    }
  }

  async function queryOutcome() {
    const requestId = generation?.request_id ?? eligibility?.active_generation_request_id;
    if (!requestId) {
      await load();
      return;
    }
    setLoading(true);
    try {
      setGeneration(await getPlanGeneration(requestId));
      setFeedbackTone("success");
      setFeedback("已查询最新处理状态。");
    } catch (error) {
      setFeedbackTone("error");
      setFeedback(getSafeSlice6Error(error).message);
    } finally {
      setLoading(false);
    }
  }

  if (!autoLoad && !eligibility && !loading) {
    return (
      <article className="rounded-xl border border-primary-100 bg-white p-5 shadow-panel">
        <h2 className="font-semibold text-slate-950">方案生成资格</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          由服务端复核评估、同意、高风险任务、主健管师和模板状态；页面不自行判定。
        </p>
        <button className={`${secondaryButtonClassName} mt-4`} onClick={() => void load()} type="button">
          核验方案生成资格
        </button>
      </article>
    );
  }

  if (loading && !eligibility) return <LoadingPanel label="正在核验方案生成资格…" />;

  return (
    <article className="rounded-xl border border-primary-100 bg-white p-5 shadow-panel">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">方案生成资格</p>
          <h2 className="mt-1 text-lg font-semibold text-slate-950">
            {eligibility?.eligible ? "已具备方案生成条件" : "暂不具备方案生成条件"}
          </h2>
        </div>
        <button className={secondaryButtonClassName} onClick={() => void load()} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新资格
        </button>
      </div>
      {eligibility?.eligible ? (
        <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
          服务端已确认当前评估、同意、风险与模板条件。生成不会产生诊断或处方。
        </div>
      ) : (
        <ul className="mt-4 space-y-2 text-sm text-amber-800">
          {(eligibility?.blocking_codes ?? []).map((code) => (
            <li className="rounded-lg bg-amber-50 px-3 py-2" key={code}>
              {blockingLabel(code)}
            </li>
          ))}
        </ul>
      )}
      <Feedback message={feedback} tone={feedbackTone} />
      <div className="mt-4 flex flex-wrap gap-2">
        {eligibility?.eligible && !generation ? (
          <button
            className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
            disabled={submitting}
            onClick={() => void generate()}
            type="button"
          >
            {submitting ? "提交中…" : "生成健康管理方案"}
          </button>
        ) : null}
        {feedback.includes("无法确认") ? (
          <button className={secondaryButtonClassName} onClick={() => void queryOutcome()} type="button">
            查询处理结果
          </button>
        ) : null}
      </div>
    </article>
  );
}

export function HealthPlanPage({ mode }: { mode: "generation" | "list" | "detail" }) {
  const { requestId = "", caseId = "", planId = "" } = useParams();
  const [generation, setGeneration] = useState<PlanGenerationRequest | null>(null);
  const [plans, setPlans] = useState<HealthPlanSummary[]>([]);
  const [detail, setDetail] = useState<HealthPlanDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState("");

  const load = useCallback(async () => {
    const id = mode === "generation" ? requestId : mode === "list" ? caseId : planId;
    if (!isUuidV7(id)) {
      setFeedback("页面标识无效，请返回上一页后重试。");
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      if (mode === "generation") setGeneration(await getPlanGeneration(toUuidV7(requestId)));
      if (mode === "list") setPlans((await listInstitutionPlans(toUuidV7(caseId), { limit: 20 })).items);
      if (mode === "detail") setDetail(await getInstitutionPlan(toUuidV7(planId)));
      setFeedback("");
    } catch (error) {
      setFeedback(getSafeSlice6Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [caseId, mode, planId, requestId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <LoadingPanel label="正在加载方案状态…" />;

  return (
    <main className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">客户服务 / 健康管理方案</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">
            {mode === "generation" ? "方案处理进度" : mode === "list" ? "健康管理方案" : "健康管理方案详情"}
          </h1>
        </div>
        <button className={secondaryButtonClassName} onClick={() => void load()} type="button">
          刷新状态
        </button>
      </header>
      <Feedback message={feedback} tone="error" />
      {generation ? <GenerationPanel generation={generation} /> : null}
      {mode === "list" ? <PlanList plans={plans} /> : null}
      {detail ? <PlanDetail detail={detail} /> : null}
    </main>
  );
}

function GenerationPanel({ generation }: { generation: PlanGenerationRequest }) {
  const label = generationStatusLabel(generation.status);
  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
      <article className="rounded-xl border border-slate-200 bg-white p-6 shadow-panel">
        <div className="flex items-center gap-3">
          <Clock3 aria-hidden="true" className="text-teal-700" size={22} />
          <div>
            <p className="text-xs text-slate-500">当前状态</p>
            <h2 className="mt-1 text-xl font-semibold">{label}</h2>
          </div>
        </div>
        <p className="mt-4 text-sm leading-6 text-slate-600">
          系统按已发布模板和已完成评估生成结构化方案；医学专家审核后才会进入用户确认。
        </p>
      </article>
      <article className="rounded-xl border border-primary-100 bg-primary-50 p-5">
        <h2 className="font-semibold text-primary-700">下一步</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          请刷新确认服务端最新状态。遇到结果未知时不要更换幂等键重复发起。
        </p>
      </article>
    </section>
  );
}

function PlanList({ plans }: { plans: HealthPlanSummary[] }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-panel">
      <div className="border-b border-slate-100 px-5 py-4">
        <h2 className="font-semibold">方案记录</h2>
      </div>
      {plans.length ? (
        plans.map((plan) => (
          <Link
            className="grid gap-2 border-b border-slate-100 px-5 py-4 last:border-0 sm:grid-cols-[1fr_auto]"
            key={plan.plan_id}
            to={`/institution/plans/${plan.plan_id}`}
          >
            <div>
              <p className="font-medium">{templateLabel(plan.template_code)}</p>
              <p className="mt-1 text-xs text-slate-500">版本 {plan.version}</p>
            </div>
            <span className="text-sm font-semibold text-teal-700">{generationStatusLabel(plan.status)}</span>
          </Link>
        ))
      ) : (
        <p className="px-5 py-10 text-center text-sm text-slate-500">当前服务案例暂无方案记录。</p>
      )}
    </section>
  );
}

function PlanDetail({ detail }: { detail: HealthPlanDetail }) {
  const sections = [
    ["方案模块", detail.module_summaries],
    ["健康目标", detail.goals],
    ["执行阶段", detail.stages],
    ["关键里程碑", detail.milestones],
    ["服务操作规范", detail.sop_items],
  ] as const;
  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="space-y-4">
        {sections.map(([title, items]) => (
          <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel" key={title}>
            <h2 className="font-semibold">{title}</h2>
            <ul className="mt-3 grid gap-2 sm:grid-cols-2">
              {items.map((item) => (
                <li className="rounded-lg bg-slate-50 px-3 py-2 text-sm" key={item.code}>
                  {item.label ?? item.code}
                </li>
              ))}
            </ul>
          </article>
        ))}
      </div>
      <aside className="space-y-4">
        <article className="rounded-xl border border-teal-200 bg-teal-50 p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" size={19} />
            <h2 className="font-semibold">医学锁定内容仅供查看</h2>
          </div>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            机构人员不能编辑目标、阶段、操作规范、禁忌或风险字段。
          </p>
        </article>
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <p className="text-xs text-slate-500">方案状态</p>
          <p className="mt-1 font-semibold">{generationStatusLabel(detail.status)}</p>
        </article>
      </aside>
    </section>
  );
}

function blockingLabel(code: string) {
  return (
    (
      {
        HIGH_RISK_BLOCKING: "高风险任务尚未解除",
        ASSESSMENT_INPUT_NOT_READY: "评估输入尚未准备完成",
        ASSESSMENT_NOT_COMPLETED: "健康评估尚未完成",
        CONSENT_NOT_CURRENT: "当前服务同意已失效",
        PRIMARY_THERAPIST_NOT_CURRENT: "主健管师分配已失效",
        TEMPLATE_NOT_AVAILABLE: "暂无适用的已发布模板",
      } as Record<string, string>
    )[code] ?? "当前业务条件发生变化，请刷新后核对"
  );
}

function generationStatusLabel(status: string) {
  return (
    (
      {
        REQUESTED: "生成请求已受理",
        GENERATING: "方案生成中",
        GENERATION_FAILED: "方案生成未完成",
        IN_REVIEW: "医学专家审核中",
        NEEDS_CORRECTION: "方案需要补正",
        APPROVED: "方案已批准",
        REJECTED: "方案未通过",
        USER_DECISION_PENDING: "等待用户确认",
        NEEDS_EXPLANATION: "用户需要解释",
        DECLINED: "用户已拒绝",
        ACCEPTED: "用户已确认",
        ACTIVE: "方案已激活",
        SUPERSEDED: "已有更新版本",
      } as Record<string, string>
    )[status] ?? "状态待核对"
  );
}

function templateLabel(code: string) {
  return ({ METABOLIC_FOUNDATION: "代谢健康基础方案" } as Record<string, string>)[code] ?? "健康管理方案";
}
