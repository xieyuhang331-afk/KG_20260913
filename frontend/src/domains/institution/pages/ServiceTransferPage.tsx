import { ArrowLeft, ArrowRightLeft, CheckCircle2, CircleAlert, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  coordinateServiceTransferClose,
  getContinuationHandoff,
  getInstitutionTransfer,
  getPlatformTransfer,
  getSafeSlice7Error,
  listInstitutionTransfers,
  listPlatformTransfers,
  rejectServiceTransfer,
  startServiceTransferReview,
  type ContinuationHandoffDTO,
  type DataScope,
  type TransferDTO,
  type TransferStatus,
} from "@/shared/api/slice7";
import { createIdempotencyKey } from "@/shared/api/slice3";
import { useAuthStore } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { CursorRecoveryAction, useRecoverableCursorPage } from "@/shared/pagination/游标分页恢复";
import { EmptyPanel, Feedback, LoadingPanel, secondaryButtonClassName } from "@/domains/institution/受控入驻界面";

type TransferMode = "institution" | "platform";
type TransferAction = "start-review" | "reject" | "coordinate-close";

export function ServiceTransferPage({ mode = "institution" }: { mode?: TransferMode }) {
  const { transferId = "" } = useParams();
  return transferId ? <TransferDetail mode={mode} transferId={transferId} /> : <TransferList mode={mode} />;
}

function TransferList({ mode }: { mode: TransferMode }) {
  const { currentUser } = useAuthStore();
  const [status, setStatus] = useState<TransferStatus | "">("");
  const requestScope = `${mode}:${currentUser?.role ?? "anonymous"}:${currentUser?.tenant_id ?? "none"}`;
  const loadPage = useCallback(
    (cursor?: string) => {
      void requestScope;
      const params = { status: status || undefined, cursor, limit: 20 };
      return mode === "institution" ? listInstitutionTransfers(params) : listPlatformTransfers(params);
    },
    [mode, requestScope, status],
  );
  const page = useRecoverableCursorPage(loadPage, getSafeSlice7Error);

  const basePath = mode === "institution" ? "/institution/service-transfers" : "/platform/service-transfers";
  return (
    <main className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">
            {mode === "institution" ? "客户服务 / 服务接续" : "平台服务监督 / 转机构协同"}
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">
            {mode === "institution" ? "转机构服务接续" : "转机构监督"}
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
            查看转出、转入、授权范围确认和接续进度；状态由服务端按当前机构范围安全返回。
          </p>
        </div>
        <button className={secondaryButtonClassName} onClick={() => void page.refresh()} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-panel">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-56 text-sm font-medium text-slate-700">
            转机构状态
            <select
              className="mt-1.5 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3"
              onChange={(event) => {
                setStatus(event.target.value as TransferStatus | "");
              }}
              value={status}
            >
              <option value="">全部状态</option>
              {transferStatuses.map((value) => (
                <option key={value} value={value}>
                  {transferStatusLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <div className="ml-auto text-xs text-slate-500">本批次 {page.items.length} 项 · 签名分页凭据原样使用</div>
        </div>
      </section>
      <Feedback message={page.feedback} tone="error" />
      <CursorRecoveryAction loading={page.loading} onRecover={page.recoverToFirstPage} visible={page.canRecover} />
      {page.loading ? (
        <LoadingPanel label="正在加载转机构记录…" />
      ) : page.items.length ? (
        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
          <div className="hidden grid-cols-[minmax(0,1.2fr)_190px_minmax(0,1fr)_110px] gap-4 border-b border-slate-100 bg-slate-50 px-5 py-3 text-xs font-semibold text-slate-500 md:grid">
            <span>服务接续</span>
            <span>当前状态</span>
            <span>授权范围</span>
            <span>操作</span>
          </div>
          <div className="divide-y divide-slate-100">
            {page.items.map((item) => (
              <div
                className="grid gap-3 px-5 py-4 md:grid-cols-[minmax(0,1.2fr)_190px_minmax(0,1fr)_110px] md:items-center"
                key={item.transfer_id}
              >
                <div>
                  <p className="font-semibold text-slate-950">转机构接续 {publicReference(item.transfer_id)}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    关联服务案例 {publicReference(item.source_service_case_id)}
                  </p>
                </div>
                <TransferBadge status={item.status} />
                <p className="text-sm text-slate-600">{scopeSummary(item.requested_scope)}</p>
                <Link className="text-sm font-semibold text-primary-600" to={`${basePath}/${item.transfer_id}`}>
                  查看详情
                </Link>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <EmptyPanel
          title="当前没有转机构记录"
          description="用户发起转机构申请后，符合当前机构或平台范围的记录会显示在这里。"
        />
      )}
      <div className="flex justify-end gap-2">
        <button
          className={secondaryButtonClassName}
          disabled={page.loading || !page.canGoBack}
          onClick={page.previous}
          type="button"
        >
          上一批
        </button>
        <button
          className={secondaryButtonClassName}
          disabled={page.loading || !page.nextCursor}
          onClick={page.next}
          type="button"
        >
          下一批
        </button>
      </div>
    </main>
  );
}

function TransferDetail({ mode, transferId }: { mode: TransferMode; transferId: string }) {
  const { currentUser } = useAuthStore();
  const [detail, setDetail] = useState<TransferDTO | null>(null);
  const [handoff, setHandoff] = useState<ContinuationHandoffDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [tone, setTone] = useState<"success" | "error">("error");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const value =
        mode === "institution" ? await getInstitutionTransfer(transferId) : await getPlatformTransfer(transferId);
      setDetail(value);
      if (mode === "institution" && currentUser?.role === USER_ROLES.orgAdmin && value.status === "TRANSFERRED") {
        try {
          setHandoff(await getContinuationHandoff(transferId));
        } catch {
          setHandoff(null);
        }
      }
      setFeedback("");
    } catch (error) {
      setFeedback(getSafeSlice7Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [currentUser?.role, mode, transferId]);

  useEffect(() => void load(), [load]);

  async function runAction(action: TransferAction) {
    if (!detail || submitting || !window.confirm(transferActionConfirmation(action))) return;
    setSubmitting(true);
    try {
      const input = { expected_version: detail.version, reason_code: transferReasonCode(action) };
      const value =
        action === "start-review"
          ? await startServiceTransferReview(transferId, input, createIdempotencyKey())
          : action === "reject"
            ? await rejectServiceTransfer(transferId, input, createIdempotencyKey())
            : await coordinateServiceTransferClose(transferId, input, createIdempotencyKey());
      setDetail(value);
      setTone("success");
      setFeedback("转机构状态已按服务端结果更新。");
    } catch (error) {
      const safe = getSafeSlice7Error(error);
      if (safe.refreshRequired) await load();
      setTone("error");
      setFeedback(safe.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <LoadingPanel label="正在加载转机构详情…" />;
  if (!detail) return <Feedback message={feedback || "未找到转机构记录。"} tone="error" />;

  const canWrite =
    mode === "institution"
      ? currentUser?.role === USER_ROLES.orgAdmin
      : currentUser?.role === USER_ROLES.superAdmin || currentUser?.role === USER_ROLES.sysAdmin;
  const actions = canWrite ? availableTransferActions(detail.status, mode) : [];
  const basePath = mode === "institution" ? "/institution/service-transfers" : "/platform/service-transfers";

  return (
    <main className="space-y-5">
      <Link className="inline-flex items-center gap-2 text-sm font-semibold text-primary-600" to={basePath}>
        <ArrowLeft aria-hidden="true" size={16} />
        返回转机构列表
      </Link>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">转机构服务接续 / 权威状态</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">
            转机构接续 {publicReference(detail.transfer_id)}
          </h1>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            仅展示授权范围、流转事实与接续结果，不展示客户原始健康数据。
          </p>
        </div>
        <button className={secondaryButtonClassName} onClick={() => void load()} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      <Feedback message={feedback} tone={tone} />
      <TransferRail status={detail.status} />
      <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex items-center gap-2">
            <ArrowRightLeft aria-hidden="true" className="text-teal-700" size={20} />
            <h2 className="font-semibold text-slate-950">接续摘要</h2>
          </div>
          <dl className="mt-5 grid gap-4 sm:grid-cols-2">
            <Summary label="当前状态" value={transferStatusLabel(detail.status)} />
            <Summary label="关联服务案例" value={publicReference(detail.source_service_case_id)} />
            <Summary label="目标机构" value={`受控机构 ${publicReference(detail.target_tenant_id)}`} />
            <Summary label="源机构" value={`受控机构 ${publicReference(detail.source_tenant_id)}`} />
            <Summary label="目标机构决定" value={safeFact(detail.target_decision)} />
            <Summary label="原机构关闭" value={safeFact(detail.source_closure_status)} />
          </dl>
          <div className="mt-5 border-t border-slate-100 pt-5">
            <p className="text-xs font-semibold text-slate-500">已授权接续范围</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {detail.requested_scope.map((scope) => (
                <span className="rounded-full bg-teal-50 px-3 py-1 text-xs font-semibold text-teal-700" key={scope}>
                  {scopeLabel(scope)}
                </span>
              ))}
            </div>
          </div>
          {handoff ? (
            <div className="mt-5 rounded-xl border border-primary-100 bg-primary-50 p-4">
              <div className="flex items-center gap-2">
                <CheckCircle2 aria-hidden="true" className="text-primary-600" size={18} />
                <h3 className="font-semibold">服务接续交接</h3>
              </div>
              <p className="mt-2 text-sm text-slate-700">{handoffStatusLabel(handoff.status)}</p>
            </div>
          ) : null}
        </article>
        <aside className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="text-teal-700" size={19} />
            <h2 className="font-semibold">受控操作</h2>
          </div>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            {canWrite ? "每次操作使用最新版本和单次幂等键；409 后只刷新。" : "当前角色仅可查看转机构进度。"}
          </p>
          <div className="mt-4 grid gap-2">
            {actions.map((action) => (
              <button
                className={
                  action === "reject"
                    ? "min-h-10 rounded-lg border border-rose-200 px-4 text-sm font-semibold text-rose-700 disabled:opacity-50"
                    : "min-h-10 rounded-lg bg-teal-700 px-4 text-sm font-semibold text-white disabled:opacity-50"
                }
                disabled={submitting}
                key={action}
                onClick={() => void runAction(action)}
                type="button"
              >
                {transferActionLabel(action)}
              </button>
            ))}
            {!actions.length ? (
              <p className="rounded-lg bg-slate-50 px-3 py-3 text-sm text-slate-600">当前状态没有可安全执行的操作。</p>
            ) : null}
          </div>
          {mode === "institution" && detail.status === "NEW_INSTITUTION_REVIEWING" ? (
            <ContractNotice text="接受转入需要服务端提供当前机构可选服务标签；当前DTO未提供，页面不会硬编码提交。" />
          ) : null}
          {mode === "institution" && detail.status === "ACCEPTED" ? (
            <ContractNotice text="原机构关闭需要权威服务总结引用；当前DTO未提供，页面不会要求手工输入内部标识。" />
          ) : null}
        </aside>
      </section>
    </main>
  );
}

export function availableTransferActions(status: string, mode: TransferMode): TransferAction[] {
  if (mode === "platform") return status === "USER_SCOPE_CONFIRMED" ? ["coordinate-close"] : [];
  if (status === "REQUESTED_BY_USER") return ["start-review"];
  if (status === "NEW_INSTITUTION_REVIEWING") return ["reject"];
  return [];
}

export function transferStatusLabel(status: string) {
  return transferLabels[status as TransferStatus] ?? "状态待核对";
}
function TransferRail({ status }: { status: TransferStatus }) {
  const steps: Array<{ key: TransferStatus; label: string }> = [
    { key: "REQUESTED_BY_USER", label: "用户已申请" },
    { key: "NEW_INSTITUTION_REVIEWING", label: "目标机构审核" },
    { key: "ACCEPTED", label: "目标机构接受" },
    { key: "OLD_INSTITUTION_CLOSING", label: "原机构关闭" },
    { key: "USER_SCOPE_CONFIRMED", label: "用户确认范围" },
    { key: "TRANSFERRED", label: "服务已接续" },
  ];
  const current = steps.findIndex((step) => step.key === status);
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
      <div className="grid md:grid-cols-6">
        {steps.map((step, index) => (
          <div className="border-b border-slate-100 px-3 py-4 last:border-0 md:border-b-0 md:border-r" key={step.key}>
            <div className="flex items-center gap-2">
              <span
                className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${index <= current ? "bg-teal-100 text-teal-700" : "bg-slate-100 text-slate-400"}`}
              >
                {index + 1}
              </span>
              <p className={`text-sm font-semibold ${index === current ? "text-slate-950" : "text-slate-500"}`}>
                {step.label}
              </p>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
function TransferBadge({ status }: { status: TransferStatus }) {
  const terminal = status === "TRANSFERRED";
  const rejected = status === "REJECTED_BY_NEW_INSTITUTION" || status === "CANCELLED_BY_USER";
  return (
    <span
      className={`w-fit rounded-full px-2.5 py-1 text-xs font-semibold ${terminal ? "bg-emerald-50 text-emerald-700" : rejected ? "bg-slate-100 text-slate-600" : "bg-amber-50 text-amber-700"}`}
    >
      {transferStatusLabel(status)}
    </span>
  );
}
function ContractNotice({ text }: { text: string }) {
  return (
    <div className="mt-4 flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">
      <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0" size={15} />
      <span>{text}</span>
    </div>
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
function scopeSummary(scopes: DataScope[]) {
  return scopes.length ? scopes.map(scopeLabel).join("、") : "无授权范围";
}
function scopeLabel(scope: DataScope) {
  return (
    {
      PROFILE: "健康档案摘要",
      REPORT: "检测报告",
      CANONICAL_FACT: "健康指标",
      ASSESSMENT: "评估结果",
      APPROVED_PLAN: "已批准方案",
      MILESTONE: "履约节点",
      SERVICE_SUMMARY: "服务总结",
    } as Record<DataScope, string>
  )[scope];
}
function safeFact(value: string | null) {
  if (!value) return "尚未形成";
  return /^[A-Z][A-Z0-9_]*$/.test(value)
    ? (({ ACCEPTED: "已接受", REJECTED: "已拒绝", CLOSED: "已关闭", READY: "已就绪" } as Record<string, string>)[
        value
      ] ?? "已记录")
    : "已记录";
}
function publicReference(value: string) {
  return value.slice(-8).toUpperCase();
}
function handoffStatusLabel(value: ContinuationHandoffDTO["status"]) {
  return (
    {
      PENDING_TARGET_ENROLLMENT: "等待目标机构建立服务接入",
      ENROLLMENT_CREATED: "目标机构已建立服务接入",
      ASSIGNMENT_PENDING: "等待主健管师分配",
      CONTINUATION_CASE_LINKED: "接续服务案例已关联",
    } as Record<ContinuationHandoffDTO["status"], string>
  )[value];
}
function transferActionLabel(action: TransferAction) {
  return (
    { "start-review": "开始审核转入", reject: "拒绝转入", "coordinate-close": "确认平台接续完成" } as Record<
      TransferAction,
      string
    >
  )[action];
}
function transferReasonCode(action: TransferAction) {
  return (
    {
      "start-review": "REVIEW_STARTED",
      reject: "SERVICE_CONTINUATION_REJECTED",
      "coordinate-close": "HANDOFF_READY",
    } as Record<TransferAction, string>
  )[action];
}
function transferActionConfirmation(action: TransferAction) {
  return action === "start-review"
    ? "确认开始审核该转机构申请？"
    : action === "reject"
      ? "确认拒绝该转入申请？请确保已完成业务复核。"
      : "确认转机构接续已完成？该操作将形成最终转机构状态。";
}

const transferStatuses: TransferStatus[] = [
  "REQUESTED_BY_USER",
  "NEW_INSTITUTION_REVIEWING",
  "ACCEPTED",
  "OLD_INSTITUTION_CLOSING",
  "USER_SCOPE_CONFIRMED",
  "TRANSFERRED",
  "REJECTED_BY_NEW_INSTITUTION",
  "CANCELLED_BY_USER",
];
const transferLabels: Record<TransferStatus, string> = {
  REQUESTED_BY_USER: "用户已申请",
  NEW_INSTITUTION_REVIEWING: "目标机构审核中",
  ACCEPTED: "目标机构已接受",
  OLD_INSTITUTION_CLOSING: "原机构关闭中",
  USER_SCOPE_CONFIRMED: "用户已确认授权范围",
  TRANSFERRED: "服务已转机构",
  REJECTED_BY_NEW_INSTITUTION: "目标机构已拒绝",
  CANCELLED_BY_USER: "用户已取消",
};
