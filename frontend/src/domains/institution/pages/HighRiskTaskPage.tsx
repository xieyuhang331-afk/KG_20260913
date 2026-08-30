import { AlertTriangle, ArrowLeft, RefreshCw, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  actInstitutionHighRiskTask,
  getInstitutionHighRiskTask,
  getPlatformHighRiskTask,
  getSafeSlice5Error,
  listInstitutionHighRiskTasks,
  listPlatformHighRiskTasks,
  type HighRiskActionCode,
  type HighRiskTaskActionRequest,
  type HighRiskTaskDTO,
  type HighRiskTaskStatus,
} from "@/shared/api/slice5";
import { createIdempotencyKey } from "@/shared/api/slice3";
import { useAuthStore } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { EmptyPanel, Feedback, LoadingPanel, secondaryButtonClassName } from "@/domains/institution/受控入驻界面";

type WorkspaceMode = "institution" | "platform";
type EvidenceAction = "REFER" | "RESOLVE";
type EvidenceInput = Pick<HighRiskTaskActionRequest, "contact_outcome_code" | "advice_code">;
type UnknownRequest = { action: HighRiskActionCode; key: string; input: HighRiskTaskActionRequest };

export function HighRiskTaskPage({ mode = "institution" }: { mode?: WorkspaceMode }) {
  const { taskId = "" } = useParams();
  return taskId ? <HighRiskTaskDetail mode={mode} taskId={taskId} /> : <HighRiskTaskList mode={mode} />;
}

function HighRiskTaskList({ mode }: { mode: WorkspaceMode }) {
  const [items, setItems] = useState<HighRiskTaskDTO[]>([]);
  const [status, setStatus] = useState<HighRiskTaskStatus | "">("");
  const [cursor, setCursor] = useState<string>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = { status: status || undefined, cursor, limit: 20 };
      const page =
        mode === "institution" ? await listInstitutionHighRiskTasks(params) : await listPlatformHighRiskTasks(params);
      setItems(page.items);
      setNextCursor(page.next_cursor);
      setFeedback("");
    } catch (error) {
      setItems([]);
      setNextCursor(null);
      setFeedback(getSafeSlice5Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [cursor, mode, status]);

  useEffect(() => void load(), [load]);

  const basePath = mode === "institution" ? "/institution/high-risk-tasks" : "/platform/high-risk-tasks";
  return (
    <main className="space-y-5">
      <PageHeading
        description={
          mode === "institution"
            ? "查看需要机构处理的高风险任务，所有状态和阻断条件均来自服务端。"
            : "只读监督机构高风险任务的处理状态，不读取客户原始健康数据。"
        }
        eyebrow={mode === "institution" ? "客户服务 / 风险处置" : "平台监督 / 风险治理"}
        onRefresh={load}
        title={mode === "institution" ? "高风险任务" : "高风险任务监督"}
      />
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-panel">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-52 text-sm font-medium text-slate-700">
            任务状态
            <select
              className="mt-1.5 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3"
              onChange={(event) => {
                setStatus(event.target.value as HighRiskTaskStatus | "");
                setCursor(undefined);
                setCursorHistory([]);
              }}
              value={status}
            >
              <option value="">全部状态</option>
              {taskStatuses.map((value) => (
                <option key={value} value={value}>
                  {taskStatusLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <p className="ml-auto text-xs text-slate-500">本批次 {items.length} 项 · 不提供虚构总页数</p>
        </div>
      </section>
      <Feedback message={feedback} tone="error" />
      {loading ? (
        <LoadingPanel label="正在加载高风险任务…" />
      ) : items.length ? (
        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
          <div className="hidden grid-cols-[minmax(0,1fr)_150px_150px_190px_100px] gap-4 border-b border-slate-100 bg-slate-50 px-5 py-3 text-xs font-semibold text-slate-500 md:grid">
            <span>风险模块</span>
            <span>状态</span>
            <span>处置分配</span>
            <span>处理期限</span>
            <span>操作</span>
          </div>
          <div className="divide-y divide-slate-100">
            {items.map((item) => (
              <div
                className="grid gap-3 px-5 py-4 md:grid-cols-[minmax(0,1fr)_150px_150px_190px_100px] md:items-center"
                key={item.task_id}
              >
                <div>
                  <p className="font-semibold text-slate-950">{item.reason_module_codes.map(moduleLabel).join("、")}</p>
                  <p className="mt-1 text-xs text-slate-500">{blockingLabel(item)}</p>
                </div>
                <StatusBadge status={item.status} />
                <p className="text-sm text-slate-700">{item.assignee === null ? "待分配" : "已分配"}</p>
                <p className="text-sm text-slate-700">{formatTime(item.due_at)}</p>
                <Link className="text-sm font-semibold text-primary-600" to={`${basePath}/${item.task_id}`}>
                  查看详情
                </Link>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <EmptyPanel title="当前没有高风险任务" description="新的高风险评估形成后，任务会按服务端权威状态出现在这里。" />
      )}
      <div className="flex justify-end gap-2">
        <button
          className={secondaryButtonClassName}
          disabled={loading || !cursorHistory.length}
          onClick={() => {
            setCursorHistory((current) => {
              const copy = [...current];
              setCursor(copy.pop());
              return copy;
            });
          }}
          type="button"
        >
          上一批
        </button>
        <button
          className={secondaryButtonClassName}
          disabled={loading || !nextCursor}
          onClick={() => {
            if (!nextCursor) return;
            setCursorHistory((current) => [...current, cursor]);
            setCursor(nextCursor);
          }}
          type="button"
        >
          下一批
        </button>
      </div>
    </main>
  );
}

function HighRiskTaskDetail({ mode, taskId }: { mode: WorkspaceMode; taskId: string }) {
  const { currentUser } = useAuthStore();
  const [detail, setDetail] = useState<HighRiskTaskDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [tone, setTone] = useState<"success" | "error">("error");
  const [evidenceAction, setEvidenceAction] = useState<EvidenceAction | null>(null);
  const [contactOutcome, setContactOutcome] = useState<EvidenceInput["contact_outcome_code"]>(null);
  const [adviceCode, setAdviceCode] = useState<EvidenceInput["advice_code"]>(null);
  const [unknownRequest, setUnknownRequest] = useState<UnknownRequest | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDetail(
        mode === "institution" ? await getInstitutionHighRiskTask(taskId) : await getPlatformHighRiskTask(taskId),
      );
      setFeedback("");
    } catch (error) {
      setFeedback(getSafeSlice5Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [mode, taskId]);

  useEffect(() => void load(), [load]);

  async function runAction(action: HighRiskActionCode, evidence?: EvidenceInput) {
    if (!detail || submitting || !window.confirm(actionConfirmation(action))) return;
    const request =
      unknownRequest?.action === action
        ? unknownRequest
        : {
            action,
            key: createIdempotencyKey(),
            input: actionPayload(action, detail.version, evidence),
          };
    setSubmitting(true);
    try {
      const updated = await actInstitutionHighRiskTask(taskId, request.input, request.key);
      setDetail(updated);
      setUnknownRequest(null);
      setEvidenceAction(null);
      setTone("success");
      setFeedback("任务状态已按权威结果更新。");
    } catch (error) {
      const safe = getSafeSlice5Error(error);
      setUnknownRequest(safe.resultUnknown ? request : null);
      if (safe.refreshRequired) await load();
      setTone("error");
      setFeedback(safe.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <LoadingPanel label="正在加载高风险任务详情…" />;
  if (!detail) return <Feedback message={feedback || "未找到高风险任务。"} tone="error" />;

  const canWrite =
    mode === "institution" &&
    (currentUser?.role === USER_ROLES.orgAdmin || currentUser?.role === USER_ROLES.orgOperator);
  const actions = canWrite ? availableHighRiskActions(detail.status) : [];
  const basePath = mode === "institution" ? "/institution/high-risk-tasks" : "/platform/high-risk-tasks";

  return (
    <main className="space-y-5">
      <Link className="inline-flex items-center gap-2 text-sm font-semibold text-primary-600" to={basePath}>
        <ArrowLeft aria-hidden="true" size={16} /> 返回任务列表
      </Link>
      <PageHeading
        description="任务状态、风险模块和阻断条件均来自服务端；页面不展示原始健康数据。"
        eyebrow={mode === "institution" ? "客户服务 / 风险处置" : "平台监督 / 风险治理"}
        onRefresh={load}
        title={mode === "institution" ? "高风险任务详情" : "高风险任务监督详情"}
      />
      <Feedback message={feedback} tone={tone} />
      <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold tracking-wide text-slate-500">当前任务状态</p>
              <h2 className="mt-1 text-xl font-semibold text-slate-950">{taskStatusLabel(detail.status)}</h2>
            </div>
            <StatusBadge status={detail.status} />
          </div>
          <dl className="mt-5 grid gap-4 border-t border-slate-100 pt-5 sm:grid-cols-2">
            <Summary label="风险模块" value={detail.reason_module_codes.map(moduleLabel).join("、")} />
            <Summary label="处置分配" value={detail.assignee === null ? "待分配" : "已分配"} />
            <Summary label="处理期限" value={formatTime(detail.due_at)} />
            <Summary label="最近处理" value={detail.last_action_at ? formatTime(detail.last_action_at) : "尚未处理"} />
            <Summary label="方案阻断" value={detail.blocking.ordinary_plan ? "暂缓普通方案" : "未阻断"} />
            <Summary label="服务关闭阻断" value={detail.blocking.case_completion ? "暂缓关闭" : "未阻断"} />
          </dl>
        </article>
        <aside className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex items-center gap-2">
            <ShieldAlert aria-hidden="true" className="text-teal-700" size={19} />
            <h2 className="font-semibold text-slate-950">{mode === "institution" ? "任务操作" : "监督边界"}</h2>
          </div>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            {mode === "platform"
              ? "平台端仅监督任务状态，不提供机构任务处置操作。"
              : "操作携带最新版本；409或提交结果未知时只查询权威状态，不自动重放。"}
          </p>
          <div className="mt-4 grid gap-2">
            {actions.map((action) => (
              <button
                className={
                  action === "RESOLVE"
                    ? "min-h-10 rounded-lg border border-rose-200 px-4 text-sm font-semibold text-rose-700 disabled:opacity-50"
                    : "min-h-10 rounded-lg bg-teal-700 px-4 text-sm font-semibold text-white disabled:opacity-50"
                }
                disabled={submitting}
                key={action}
                onClick={() => {
                  if (action === "REFER" || action === "RESOLVE") {
                    setEvidenceAction(action);
                    return;
                  }
                  void runAction(action);
                }}
                type="button"
              >
                {actionLabel(action)}
              </button>
            ))}
            {evidenceAction ? (
              <div className="mt-2 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
                <p className="text-sm font-semibold text-slate-800">{actionLabel(evidenceAction)}证据</p>
                <label className="block text-xs font-medium text-slate-600">
                  联系结果
                  <select
                    className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                    onChange={(event) =>
                      setContactOutcome((event.target.value || null) as EvidenceInput["contact_outcome_code"])
                    }
                    value={contactOutcome ?? ""}
                  >
                    <option value="">请选择</option>
                    <option value="CONTACTED">已联系</option>
                    <option value="UNABLE_TO_CONTACT">暂时无法联系</option>
                    <option value="NOT_REQUIRED">本次无需联系</option>
                  </select>
                </label>
                <label className="block text-xs font-medium text-slate-600">
                  结构化建议
                  <select
                    className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm"
                    onChange={(event) => setAdviceCode((event.target.value || null) as EvidenceInput["advice_code"])}
                    value={adviceCode ?? ""}
                  >
                    <option value="">请选择</option>
                    <option value="PROMPT_ARTIFICIAL_REVIEW">提示人工复核</option>
                    <option value="PROMPT_MEDICAL_CONTACT">提示联系医学负责人</option>
                    <option value="PROMPT_EMERGENCY_IF_ACUTE">急性情况提示紧急处置</option>
                    <option value="PROMPT_LOW_GLUCOSE_SAFE_INTAKE">低血糖安全摄入提示</option>
                  </select>
                </label>
                <button
                  className="min-h-10 w-full rounded-lg bg-teal-700 px-4 text-sm font-semibold text-white disabled:opacity-50"
                  disabled={submitting || !contactOutcome || !adviceCode}
                  onClick={() =>
                    void runAction(evidenceAction, {
                      contact_outcome_code: contactOutcome,
                      advice_code: adviceCode,
                    })
                  }
                  type="button"
                >
                  确认提交{actionLabel(evidenceAction)}
                </button>
              </div>
            ) : null}
            {!actions.length ? (
              <p className="rounded-lg bg-slate-50 px-3 py-3 text-sm text-slate-600">当前状态没有可执行操作。</p>
            ) : null}
          </div>
        </aside>
      </section>
    </main>
  );
}

export function availableHighRiskActions(status: string): HighRiskActionCode[] {
  if (status === "OPEN") return ["CLAIM", "REFER", "RESOLVE"];
  if (status === "CLAIMED") return ["ESCALATE", "REFER", "RESOLVE"];
  if (status === "ESCALATED") return ["REFER", "RESOLVE"];
  return [];
}

function actionPayload(
  action: HighRiskActionCode,
  version: number,
  evidence?: EvidenceInput,
): HighRiskTaskActionRequest {
  const base = { expected_version: version, action_code: action, occurred_at: new Date().toISOString() };
  if (action === "CLAIM")
    return { ...base, contact_outcome_code: null, advice_code: null, reason_code: "CLAIMED_FOR_REVIEW" as const };
  if (action === "ESCALATE")
    return { ...base, contact_outcome_code: null, advice_code: null, reason_code: "SAFETY_STATE_UNCONFIRMED" as const };
  if (action === "REFER")
    return {
      ...base,
      contact_outcome_code: evidence?.contact_outcome_code ?? null,
      advice_code: evidence?.advice_code ?? null,
      reason_code: "REFERRED_TO_MEDICAL_RESPONSIBLE_PERSON" as const,
    };
  return {
    ...base,
    contact_outcome_code: evidence?.contact_outcome_code ?? null,
    advice_code: evidence?.advice_code ?? null,
    reason_code: "CURRENT_REASSESSMENT_NON_HIGH_RISK" as const,
  };
}

function PageHeading({
  eyebrow,
  title,
  description,
  onRefresh,
}: {
  eyebrow: string;
  title: string;
  description: string;
  onRefresh: () => void | Promise<void>;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <p className="text-xs font-semibold tracking-wide text-teal-700">{eyebrow}</p>
        <h1 className="mt-1 text-2xl font-semibold text-slate-950">{title}</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{description}</p>
      </div>
      <button className={secondaryButtonClassName} onClick={() => void onRefresh()} type="button">
        <RefreshCw aria-hidden="true" className="mr-2" size={16} />
        刷新
      </button>
    </header>
  );
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "RESOLVED"
      ? "bg-emerald-50 text-emerald-700"
      : status === "REFERRED" || status === "ESCALATED"
        ? "bg-amber-50 text-amber-700"
        : "bg-rose-50 text-rose-700";
  return (
    <span className={`inline-flex w-fit items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>
      <AlertTriangle aria-hidden="true" size={13} />
      {taskStatusLabel(status)}
    </span>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 font-medium text-slate-800">{value}</dd>
    </div>
  );
}

function blockingLabel(item: HighRiskTaskDTO) {
  if (item.blocking.ordinary_plan && item.blocking.case_completion) return "阻断普通方案与服务关闭";
  if (item.blocking.ordinary_plan) return "阻断普通方案";
  if (item.blocking.case_completion) return "阻断服务关闭";
  return "当前无流程阻断";
}

export function taskStatusLabel(status: string) {
  return (
    (
      { OPEN: "待处理", CLAIMED: "处理中", ESCALATED: "已升级", REFERRED: "已转介", RESOLVED: "已解除" } as Record<
        string,
        string
      >
    )[status] ?? "状态待核对"
  );
}

function moduleLabel(code: string) {
  return (
    (
      {
        BLOOD_PRESSURE_CARDIOVASCULAR: "血压与心血管",
        GLUCOSE_METABOLISM: "血糖代谢",
        LIPID_METABOLISM: "血脂代谢",
        WEIGHT_ABDOMINAL_OBESITY: "体重与腹型肥胖",
      } as Record<string, string>
    )[code] ?? "风险模块待核对"
  );
}

function actionLabel(action: HighRiskActionCode) {
  return ({ CLAIM: "领取处理", ESCALATE: "升级处理", REFER: "转介医学负责人", RESOLVE: "解除任务" } as const)[action];
}

function actionConfirmation(action: HighRiskActionCode) {
  return (
    {
      CLAIM: "确认领取当前高风险任务？",
      ESCALATE: "确认升级当前高风险任务？",
      REFER: "确认转介至医学负责人？",
      RESOLVE: "确认风险状态已复核并解除任务？",
    } as const
  )[action];
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待核对"
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

const taskStatuses: HighRiskTaskStatus[] = ["OPEN", "CLAIMED", "ESCALATED", "REFERRED", "RESOLVED"];
