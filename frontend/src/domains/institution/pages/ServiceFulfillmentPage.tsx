import { AlertTriangle, ArrowLeft, CheckCircle2, Clock3, RefreshCw, ShieldAlert, Square } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getInstitutionServiceCase,
  getPlatformServiceCase,
  getSafeSlice7Error,
  listInstitutionServiceCases,
  listPlatformServiceCases,
  pauseInstitutionServiceCase,
  resumeInstitutionServiceCase,
  safetyTerminateServiceCase,
  terminateInstitutionServiceCase,
  type ClosingReadiness,
  type MilestoneCode,
  type MilestoneDTO,
  type ServiceFulfillmentDTO,
  type ServiceLifecycleStatus,
} from "@/shared/api/slice7";
import { createIdempotencyKey } from "@/shared/api/slice3";
import { useAuthStore } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { EmptyPanel, Feedback, LoadingPanel, secondaryButtonClassName } from "@/domains/institution/受控入驻界面";

type WorkspaceMode = "institution" | "platform";
type CaseAction = "pause" | "resume" | "terminate" | "safety-terminate";

const milestoneOrder: MilestoneCode[] = ["D0", "D7", "D14", "D21", "D28"];

export function ServiceFulfillmentPage({ mode = "institution" }: { mode?: WorkspaceMode }) {
  const { caseId = "" } = useParams();
  return caseId ? <FulfillmentDetail caseId={caseId} mode={mode} /> : <FulfillmentList mode={mode} />;
}

function FulfillmentList({ mode }: { mode: WorkspaceMode }) {
  const [items, setItems] = useState<ServiceFulfillmentDTO[]>([]);
  const [status, setStatus] = useState<ServiceLifecycleStatus | "">("");
  const [riskOnly, setRiskOnly] = useState(false);
  const [cursor, setCursor] = useState<string>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        cursor,
        limit: 20,
        status: status || undefined,
        risk: riskOnly ? ("AT_RISK" as const) : undefined,
      };
      const page =
        mode === "institution" ? await listInstitutionServiceCases(params) : await listPlatformServiceCases(params);
      setItems(page.items);
      setNextCursor(page.next_cursor);
      setFeedback("");
    } catch (error) {
      setFeedback(getSafeSlice7Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [cursor, mode, riskOnly, status]);

  useEffect(() => void load(), [load]);

  const basePath = mode === "institution" ? "/institution/service-cases" : "/platform/service-fulfillment";
  const platform = mode === "platform";

  return (
    <main className="space-y-5">
      <PageHeading
        eyebrow={platform ? "平台服务监督 / 履约全局" : "客户服务 / 履约执行"}
        title={platform ? "服务履约监督" : "服务履约看板"}
        description={
          platform
            ? "监督服务案例的履约周期、高风险阻断与关闭准备，不读取客户原始健康隐私。"
            : "按服务端权威状态查看当前服务周期、五阶段里程碑和需要机构处理的风险。"
        }
        onRefresh={load}
      />

      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-panel">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-48 text-sm font-medium text-slate-700">
            服务状态
            <select
              className="mt-1.5 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3"
              onChange={(event) => {
                setStatus(event.target.value as ServiceLifecycleStatus | "");
                setCursor(undefined);
                setCursorHistory([]);
              }}
              value={status}
            >
              <option value="">全部状态</option>
              {lifecycleOptions.map((value) => (
                <option key={value} value={value}>
                  {serviceLifecycleLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex min-h-10 items-center gap-2 rounded-lg border border-slate-200 px-3 text-sm font-medium text-slate-700">
            <input
              checked={riskOnly}
              onChange={(event) => {
                setRiskOnly(event.target.checked);
                setCursor(undefined);
                setCursorHistory([]);
              }}
              type="checkbox"
            />
            仅看高风险阻断
          </label>
          <div className="ml-auto text-xs text-slate-500">本批次 {items.length} 项 · 不提供虚构总页数</div>
        </div>
      </section>

      <Feedback message={feedback} tone="error" />
      {loading ? (
        <LoadingPanel label="正在加载服务履约状态…" />
      ) : items.length ? (
        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
          <div className="hidden grid-cols-[minmax(0,1.3fr)_170px_160px_160px_110px] gap-4 border-b border-slate-100 bg-slate-50 px-5 py-3 text-xs font-semibold text-slate-500 md:grid">
            <span>服务案例</span>
            <span>生命周期</span>
            <span>履约进度</span>
            <span>关闭准备</span>
            <span>操作</span>
          </div>
          <div className="divide-y divide-slate-100">
            {items.map((item) => (
              <div
                className="grid gap-3 px-5 py-4 md:grid-cols-[minmax(0,1.3fr)_170px_160px_160px_110px] md:items-center"
                key={item.service_case_id}
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-semibold text-slate-950">服务案例 {publicReference(item.service_case_id)}</p>
                    {item.risk_flag === "AT_RISK" ? <RiskBadge /> : null}
                  </div>
                  <p className="mt-1 text-xs text-slate-500">服务周期状态由服务端生成</p>
                </div>
                <StatusBadge
                  label={serviceLifecycleLabel(item.lifecycle_status)}
                  tone={lifecycleTone(item.lifecycle_status)}
                />
                <p className="text-sm text-slate-700">{completedMilestones(item)} / 5 阶段</p>
                <p className="text-sm text-slate-700">{closingReadinessLabel(item.closing_readiness)}</p>
                <Link
                  className="text-sm font-semibold text-primary-600 hover:text-primary-700"
                  to={`${basePath}/${item.service_case_id}${mode === "institution" ? "/fulfillment" : ""}`}
                >
                  查看详情
                </Link>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <EmptyPanel
          title={riskOnly ? "当前没有高风险履约案例" : "当前没有服务履约记录"}
          description={
            riskOnly ? "可以取消高风险筛选查看全部服务案例。" : "当服务方案激活并形成履约周期后，记录会出现在这里。"
          }
        />
      )}

      <CursorControls
        canPrevious={cursorHistory.length > 0}
        canNext={Boolean(nextCursor)}
        disabled={loading}
        onNext={() => {
          if (!nextCursor) return;
          setCursorHistory((current) => [...current, cursor]);
          setCursor(nextCursor);
        }}
        onPrevious={() => {
          setCursorHistory((current) => {
            const copy = [...current];
            setCursor(copy.pop());
            return copy;
          });
        }}
      />
    </main>
  );
}

function FulfillmentDetail({ caseId, mode }: { caseId: string; mode: WorkspaceMode }) {
  const { currentUser } = useAuthStore();
  const [detail, setDetail] = useState<ServiceFulfillmentDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [tone, setTone] = useState<"success" | "error">("error");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDetail(
        mode === "institution" ? await getInstitutionServiceCase(caseId) : await getPlatformServiceCase(caseId),
      );
      setFeedback("");
    } catch (error) {
      setFeedback(getSafeSlice7Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [caseId, mode]);

  useEffect(() => void load(), [load]);

  async function runAction(action: CaseAction) {
    if (!detail || submitting || !window.confirm(actionConfirmation(action))) return;
    setSubmitting(true);
    try {
      const input = { expected_version: detail.version, reason_code: caseReasonCode(action) };
      const updated =
        action === "pause"
          ? await pauseInstitutionServiceCase(caseId, input, createIdempotencyKey())
          : action === "resume"
            ? await resumeInstitutionServiceCase(caseId, input, createIdempotencyKey())
            : action === "terminate"
              ? await terminateInstitutionServiceCase(caseId, input, createIdempotencyKey())
              : await safetyTerminateServiceCase(caseId, input, createIdempotencyKey());
      setDetail(updated);
      setTone("success");
      setFeedback("服务状态已按权威结果更新。");
    } catch (error) {
      const safe = getSafeSlice7Error(error);
      if (safe.refreshRequired) await load();
      setTone("error");
      setFeedback(safe.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <LoadingPanel label="正在加载履约详情…" />;
  if (!detail) return <Feedback message={feedback || "未找到服务履约详情。"} tone="error" />;

  const canWrite =
    mode === "institution"
      ? currentUser?.role === USER_ROLES.orgAdmin
      : currentUser?.role === USER_ROLES.superAdmin || currentUser?.role === USER_ROLES.sysAdmin;
  const actions = canWrite ? availableCaseActions(detail.lifecycle_status, mode) : [];
  const basePath = mode === "institution" ? "/institution/service-cases" : "/platform/service-fulfillment";

  return (
    <main className="space-y-5">
      <Link className="inline-flex items-center gap-2 text-sm font-semibold text-primary-600" to={basePath}>
        <ArrowLeft aria-hidden="true" size={16} /> 返回履约列表
      </Link>
      <PageHeading
        eyebrow={mode === "institution" ? "客户服务 / 履约详情" : "平台服务监督 / 履约详情"}
        title={`服务案例 ${publicReference(detail.service_case_id)}`}
        description="五阶段节点、风险阻断和关闭准备均来自服务端权威状态。"
        onRefresh={load}
      />
      <Feedback message={feedback} tone={tone} />
      <MilestoneRail detail={detail} />
      <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold tracking-wide text-slate-500">当前服务周期</p>
              <h2 className="mt-1 text-xl font-semibold text-slate-950">
                {serviceLifecycleLabel(detail.lifecycle_status)}
              </h2>
            </div>
            {detail.risk_flag === "AT_RISK" ? <RiskBadge /> : <StatusBadge label="未标记高风险" tone="success" />}
          </div>
          <dl className="mt-5 grid gap-4 border-t border-slate-100 pt-5 sm:grid-cols-2">
            <Summary label="周期起点" value={formatOptionalTime(detail.cycle_anchor_at)} />
            <Summary label="计划状态" value={detail.active_plan_id ? "已有活动方案" : "等待活动方案"} />
            <Summary label="开放高风险任务" value={`${detail.open_high_risk_count} 项`} />
            <Summary label="关闭准备" value={closingReadinessLabel(detail.closing_readiness)} />
          </dl>
        </article>
        <aside className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex items-center gap-2">
            <ShieldAlert aria-hidden="true" className="text-teal-700" size={19} />
            <h2 className="font-semibold text-slate-950">状态操作</h2>
          </div>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            {canWrite
              ? "操作提交服务端最新版本；冲突后只刷新，不自动重放。"
              : "当前角色为只读，状态操作由机构管理员或平台治理角色执行。"}
          </p>
          <div className="mt-4 grid gap-2">
            {actions.map((action) => (
              <button
                className={
                  action.includes("terminate")
                    ? "min-h-10 rounded-lg border border-rose-200 px-4 text-sm font-semibold text-rose-700 disabled:opacity-50"
                    : "min-h-10 rounded-lg bg-teal-700 px-4 text-sm font-semibold text-white disabled:opacity-50"
                }
                disabled={submitting}
                key={action}
                onClick={() => void runAction(action)}
                type="button"
              >
                {caseActionLabel(action)}
              </button>
            ))}
            {!actions.length ? (
              <p className="rounded-lg bg-slate-50 px-3 py-3 text-sm text-slate-600">当前状态没有可执行操作。</p>
            ) : null}
          </div>
        </aside>
      </section>
    </main>
  );
}

export function availableCaseActions(status: string, mode: WorkspaceMode): CaseAction[] {
  if (mode === "platform")
    return status === "ACTIVE" || status === "PAUSED" || status === "CLOSING" ? ["safety-terminate"] : [];
  if (status === "ACTIVE") return ["pause", "terminate"];
  if (status === "PAUSED") return ["resume", "terminate"];
  if (status === "CLOSING") return ["terminate"];
  return [];
}

export function serviceLifecycleLabel(status: string) {
  return lifecycleLabels[status as ServiceLifecycleStatus] ?? "状态待核对";
}

function MilestoneRail({ detail }: { detail: ServiceFulfillmentDTO }) {
  const map = new Map(detail.milestones.map((item) => [item.code, item]));
  return (
    <section
      aria-label="D0至D28服务里程碑"
      className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel"
    >
      <div className="grid sm:grid-cols-5">
        {milestoneOrder.map((code, index) => {
          const item = map.get(code);
          return (
            <div
              className="relative border-b border-slate-100 px-4 py-4 last:border-0 sm:border-b-0 sm:border-r"
              key={code}
            >
              <div className="flex items-center gap-2">
                <MilestoneIcon status={item?.status} />
                <div>
                  <p className="text-xs font-semibold text-slate-500">阶段 {index + 1}</p>
                  <p className="font-semibold text-slate-950">{code}</p>
                </div>
              </div>
              <p className="mt-3 text-sm font-medium text-slate-700">{milestoneStatusLabel(item?.status)}</p>
              <p className="mt-1 text-xs text-slate-400">
                {item ? `${item.window_start} 至 ${item.window_end}` : "尚未形成周期"}
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function MilestoneIcon({ status }: { status?: MilestoneDTO["status"] }) {
  const classes = "flex h-8 w-8 items-center justify-center rounded-full";
  if (status === "COMPLETED")
    return (
      <span className={`${classes} bg-emerald-100 text-emerald-700`}>
        <CheckCircle2 aria-hidden="true" size={17} />
      </span>
    );
  if (status === "DUE")
    return (
      <span className={`${classes} bg-blue-100 text-primary-600`}>
        <Clock3 aria-hidden="true" size={17} />
      </span>
    );
  if (status === "MISSED")
    return (
      <span className={`${classes} bg-rose-100 text-rose-700`}>
        <AlertTriangle aria-hidden="true" size={17} />
      </span>
    );
  return (
    <span className={`${classes} bg-slate-100 text-slate-500`}>
      <Square aria-hidden="true" size={15} />
    </span>
  );
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

function CursorControls({
  canPrevious,
  canNext,
  disabled,
  onPrevious,
  onNext,
}: {
  canPrevious: boolean;
  canNext: boolean;
  disabled: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <div className="flex justify-end gap-2">
      <button
        className={secondaryButtonClassName}
        disabled={disabled || !canPrevious}
        onClick={onPrevious}
        type="button"
      >
        上一批
      </button>
      <button className={secondaryButtonClassName} disabled={disabled || !canNext} onClick={onNext} type="button">
        下一批
      </button>
    </div>
  );
}

function StatusBadge({ label, tone }: { label: string; tone: "success" | "warning" | "danger" | "neutral" }) {
  const classes =
    tone === "success"
      ? "bg-emerald-50 text-emerald-700"
      : tone === "warning"
        ? "bg-amber-50 text-amber-700"
        : tone === "danger"
          ? "bg-rose-50 text-rose-700"
          : "bg-slate-100 text-slate-600";
  return <span className={`inline-flex w-fit rounded-full px-2.5 py-1 text-xs font-semibold ${classes}`}>{label}</span>;
}

function RiskBadge() {
  return (
    <span className="inline-flex w-fit items-center gap-1 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-700">
      <AlertTriangle aria-hidden="true" size={13} />
      高风险阻断
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
function completedMilestones(item: ServiceFulfillmentDTO) {
  return item.milestones.filter((milestone) => milestone.status === "COMPLETED").length;
}
function publicReference(value: string) {
  return value.slice(-8).toUpperCase();
}
function formatOptionalTime(value: string | null) {
  if (!value) return "尚未形成";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待核对"
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function milestoneStatusLabel(status?: MilestoneDTO["status"]) {
  return (
    (
      {
        PENDING: "待执行",
        DUE: "当前待完成",
        COMPLETED: "已完成",
        MISSED: "已错过窗口",
        INVALIDATED: "已失效",
      } as Record<string, string>
    )[status ?? ""] ?? "尚未开始"
  );
}
function closingReadinessLabel(value: ClosingReadiness) {
  return (
    {
      NOT_READY: "尚未进入关闭准备",
      READY_TO_CLOSE: "已具备关闭条件",
      BLOCKED_BY_HIGH_RISK: "高风险任务未解除",
      BLOCKED_BY_MISSING_MILESTONE: "里程碑尚未完整",
      BLOCKED_BY_USER_ACK: "等待用户确认服务总结",
    } as Record<ClosingReadiness, string>
  )[value];
}
function lifecycleTone(value: ServiceLifecycleStatus): "success" | "warning" | "danger" | "neutral" {
  if (value === "ACTIVE" || value === "COMPLETED" || value === "TRANSFERRED") return "success";
  if (value === "PAUSED" || value === "CLOSING" || value === "PLAN_PENDING") return "warning";
  if (value === "SAFETY_TERMINATED" || value === "UNABLE_TO_CONTACT") return "danger";
  return "neutral";
}
function caseActionLabel(action: CaseAction) {
  return (
    { pause: "暂停服务", resume: "恢复服务", terminate: "终止机构服务", "safety-terminate": "安全终止服务" } as Record<
      CaseAction,
      string
    >
  )[action];
}
function caseReasonCode(action: CaseAction) {
  return (
    {
      pause: "INSTITUTION_SERVICE_PAUSED",
      resume: "INSTITUTION_SERVICE_RESUMED",
      terminate: "INSTITUTION_SERVICE_TERMINATED",
      "safety-terminate": "PLATFORM_SAFETY_TERMINATED",
    } as Record<CaseAction, string>
  )[action];
}
function actionConfirmation(action: CaseAction) {
  return action === "pause"
    ? "确认暂停当前服务？暂停后需由机构管理员主动恢复。"
    : action === "resume"
      ? "确认恢复当前服务？"
      : action === "terminate"
        ? "确认终止当前机构服务？该操作会改变服务生命周期。"
        : "确认执行安全终止？仅应在风险治理条件满足时操作。";
}

const lifecycleOptions: ServiceLifecycleStatus[] = [
  "PLAN_PENDING",
  "ACTIVE",
  "PAUSED",
  "CLOSING",
  "COMPLETED",
  "WITHDRAWN_BY_USER",
  "TERMINATED_BY_INSTITUTION",
  "TRANSFERRED",
  "UNABLE_TO_CONTACT",
  "SAFETY_TERMINATED",
];
const lifecycleLabels: Record<ServiceLifecycleStatus, string> = {
  PLAN_PENDING: "等待方案激活",
  ACTIVE: "服务进行中",
  PAUSED: "服务已暂停",
  CLOSING: "服务关闭处理中",
  COMPLETED: "服务已完成",
  WITHDRAWN_BY_USER: "用户已退出",
  TERMINATED_BY_INSTITUTION: "机构已终止",
  TRANSFERRED: "已转至新机构",
  UNABLE_TO_CONTACT: "暂时无法联系",
  SAFETY_TERMINATED: "已安全终止",
};
