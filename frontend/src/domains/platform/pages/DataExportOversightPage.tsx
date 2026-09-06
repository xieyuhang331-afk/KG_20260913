import { ArrowLeft, FileArchive, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getPlatformDataExport,
  getSafeSlice7Error,
  listPlatformDataExports,
  type DataExportDTO,
  type DataExportStatus,
  type DataScope,
} from "@/shared/api/slice7";
import { EmptyPanel, Feedback, LoadingPanel, secondaryButtonClassName } from "@/domains/institution/受控入驻界面";
import { useAuthStore } from "@/shared/auth/authStore";
import { CursorRecoveryAction, useRecoverableCursorPage } from "@/shared/pagination/游标分页恢复";

export function DataExportOversightPage() {
  const { exportId = "" } = useParams();
  return exportId ? <ExportDetail exportId={exportId} /> : <ExportList />;
}

function ExportList() {
  const { currentUser } = useAuthStore();
  const [status, setStatus] = useState<DataExportStatus | "">("");
  const requestScope = `${currentUser?.role ?? "anonymous"}:${currentUser?.tenant_id ?? "none"}`;
  const loadPage = useCallback(
    (cursor?: string) => {
      void requestScope;
      return listPlatformDataExports({ status: status || undefined, cursor, limit: 20 });
    },
    [requestScope, status],
  );
  const page = useRecoverableCursorPage(loadPage, getSafeSlice7Error);

  return (
    <main className="space-y-5">
      <Header onRefresh={page.refresh} title="数据导出任务监督" />
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-panel">
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-56 text-sm font-medium text-slate-700">
            任务状态
            <select
              className="mt-1.5 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3"
              onChange={(event) => {
                setStatus(event.target.value as DataExportStatus | "");
              }}
              value={status}
            >
              <option value="">全部状态</option>
              {exportStatuses.map((value) => (
                <option key={value} value={value}>
                  {exportStatusLabel(value)}
                </option>
              ))}
            </select>
          </label>
          <p className="ml-auto text-xs text-slate-500">只读监督 · 不提供下载凭据或文件路径</p>
        </div>
      </section>
      <Feedback message={page.feedback} tone="error" />
      <CursorRecoveryAction loading={page.loading} onRecover={page.recoverToFirstPage} visible={page.canRecover} />
      {page.loading ? (
        <LoadingPanel label="正在加载导出任务…" />
      ) : page.items.length ? (
        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
          <div className="hidden grid-cols-[minmax(0,1fr)_160px_minmax(0,1fr)_180px_100px] gap-4 border-b border-slate-100 bg-slate-50 px-5 py-3 text-xs font-semibold text-slate-500 md:grid">
            <span>导出任务</span>
            <span>状态</span>
            <span>授权范围</span>
            <span>申请时间</span>
            <span>操作</span>
          </div>
          <div className="divide-y divide-slate-100">
            {page.items.map((item) => (
              <div
                className="grid gap-3 px-5 py-4 md:grid-cols-[minmax(0,1fr)_160px_minmax(0,1fr)_180px_100px] md:items-center"
                key={item.export_id}
              >
                <div>
                  <p className="font-semibold text-slate-950">个人数据导出 {publicReference(item.export_id)}</p>
                  <p className="mt-1 text-xs text-slate-500">服务端受控生成与有效期管理</p>
                </div>
                <ExportBadge status={item.status} />
                <p className="text-sm text-slate-600">{item.requested_scope.map(scopeLabel).join("、")}</p>
                <p className="text-sm text-slate-600">{formatTime(item.requested_at)}</p>
                <Link
                  className="text-sm font-semibold text-primary-600"
                  to={`/platform/data-exports/${item.export_id}`}
                >
                  查看详情
                </Link>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <EmptyPanel
          title="当前没有导出任务"
          description="用户通过正式授权路径创建个人数据导出任务后，平台可在此监督处理状态。"
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

function ExportDetail({ exportId }: { exportId: string }) {
  const [detail, setDetail] = useState<DataExportDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDetail(await getPlatformDataExport(exportId));
      setFeedback("");
    } catch (error) {
      setFeedback(getSafeSlice7Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [exportId]);
  useEffect(() => void load(), [load]);
  if (loading) return <LoadingPanel label="正在加载导出任务详情…" />;
  if (!detail) return <Feedback message={feedback || "未找到导出任务。"} tone="error" />;
  return (
    <main className="space-y-5">
      <Link
        className="inline-flex items-center gap-2 text-sm font-semibold text-primary-600"
        to="/platform/data-exports"
      >
        <ArrowLeft aria-hidden="true" size={16} />
        返回导出任务
      </Link>
      <Header onRefresh={load} title={`数据导出任务 ${publicReference(detail.export_id)}`} />
      <Feedback message={feedback} tone="error" />
      <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex items-center gap-2">
            <FileArchive aria-hidden="true" className="text-teal-700" size={20} />
            <h2 className="font-semibold text-slate-950">任务摘要</h2>
          </div>
          <dl className="mt-5 grid gap-4 sm:grid-cols-2">
            <Summary label="当前状态" value={exportStatusLabel(detail.status)} />
            <Summary label="申请时间" value={formatTime(detail.requested_at)} />
            <Summary label="准备完成" value={formatOptionalTime(detail.ready_at)} />
            <Summary label="有效期至" value={formatOptionalTime(detail.expires_at)} />
            <Summary label="下载完成" value={formatOptionalTime(detail.downloaded_at)} />
            <Summary label="授权范围" value={`${detail.requested_scope.length} 项`} />
          </dl>
          <div className="mt-5 flex flex-wrap gap-2 border-t border-slate-100 pt-5">
            {detail.requested_scope.map((scope) => (
              <span className="rounded-full bg-teal-50 px-3 py-1 text-xs font-semibold text-teal-700" key={scope}>
                {scopeLabel(scope)}
              </span>
            ))}
          </div>
        </article>
        <aside className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="text-teal-700" size={19} />
            <h2 className="font-semibold">安全边界</h2>
          </div>
          <p className="mt-3 text-sm leading-6 text-slate-600">
            平台仅监督任务状态，不展示永久文件地址、一次性下载凭据、客户原始数据或内部存储路径。
          </p>
          <div className="mt-4">
            <ExportBadge status={detail.status} />
          </div>
        </aside>
      </section>
    </main>
  );
}

function Header({ title, onRefresh }: { title: string; onRefresh: () => void | Promise<void> }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <p className="text-xs font-semibold tracking-wide text-teal-700">平台服务监督 / 数据权益</p>
        <h1 className="mt-1 text-2xl font-semibold text-slate-950">{title}</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
          监督个人数据导出任务的生成、有效期和完成状态，不提供文件下载操作。
        </p>
      </div>
      <button className={secondaryButtonClassName} onClick={() => void onRefresh()} type="button">
        <RefreshCw aria-hidden="true" className="mr-2" size={16} />
        刷新
      </button>
    </header>
  );
}
function ExportBadge({ status }: { status: DataExportStatus }) {
  const success = status === "READY" || status === "DOWNLOADED";
  const danger = status === "FAILED";
  return (
    <span
      className={`w-fit rounded-full px-2.5 py-1 text-xs font-semibold ${success ? "bg-emerald-50 text-emerald-700" : danger ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-700"}`}
    >
      {exportStatusLabel(status)}
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
export function exportStatusLabel(status: string) {
  return exportLabels[status as DataExportStatus] ?? "状态待核对";
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
function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待核对"
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
function formatOptionalTime(value: string | null) {
  return value ? formatTime(value) : "尚未形成";
}
function publicReference(value: string) {
  return value.slice(-8).toUpperCase();
}
const exportStatuses: DataExportStatus[] = [
  "REQUESTED",
  "GENERATING",
  "READY",
  "DOWNLOADED",
  "EXPIRED",
  "FAILED",
  "CANCELLED",
];
const exportLabels: Record<DataExportStatus, string> = {
  REQUESTED: "已申请",
  GENERATING: "生成中",
  READY: "已准备",
  DOWNLOADED: "已下载",
  EXPIRED: "已过期",
  FAILED: "生成失败",
  CANCELLED: "已取消",
};
