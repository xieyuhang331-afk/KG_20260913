import { useQuery } from "@tanstack/react-query";
import { ChevronRight, FileText } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { listMyTenantApplications } from "../api";
import { applicationStatusClassName, applicationStatusLabel } from "../status";
import type { MyTenantApplicationItem, TenantApplicationStatus } from "../types";

const PAGE_SIZE = 20;
const validStatuses = new Set<TenantApplicationStatus>(["pending", "active", "rejected"]);

export function MyApplicationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedStatus = searchParams.get("status");
  const status =
    requestedStatus && validStatuses.has(requestedStatus as TenantApplicationStatus)
      ? (requestedStatus as TenantApplicationStatus)
      : undefined;
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);

  const applicationsQuery = useQuery({
    queryKey: ["institution", "my-applications", status ?? "all", page],
    queryFn: () => listMyTenantApplications({ status, page, page_size: PAGE_SIZE })
  });

  const total = applicationsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function setStatus(nextStatus: string) {
    const next = new URLSearchParams();
    if (nextStatus) next.set("status", nextStatus);
    next.set("page", "1");
    setSearchParams(next);
  }

  function setPage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(nextPage));
    setSearchParams(next);
  }

  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-xl border border-pine/10 bg-pine px-6 py-7 text-white shadow-sm">
        <div className="max-w-2xl">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-mint">Application Desk</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-tight">我的门店申请</h1>
          <p className="mt-3 text-sm leading-6 text-white/75">
            查看当前机构提交的门店入驻申请，以及平台审核后的最新状态。
          </p>
        </div>
      </section>

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="font-semibold text-ink">申请记录</h2>
            <p className="mt-1 text-xs text-slate-500">共 {total} 条属于当前机构的申请</p>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <span>状态</span>
            <select
              className="h-9 rounded-md border border-slate-200 bg-white px-3 outline-none focus:border-pine focus:ring-2 focus:ring-mint"
              onChange={(event) => setStatus(event.target.value)}
              value={status ?? ""}
            >
              <option value="">全部</option>
              <option value="pending">审核中</option>
              <option value="active">已通过</option>
              <option value="rejected">已驳回</option>
            </select>
          </label>
        </div>

        {applicationsQuery.isLoading ? (
          <div className="p-8 text-sm text-slate-500">正在加载申请记录...</div>
        ) : applicationsQuery.isError ? (
          <div className="p-8 text-sm text-red-700">申请记录加载失败，请刷新页面重试。</div>
        ) : applicationsQuery.data?.items.length ? (
          <div className="divide-y divide-slate-100">
            {applicationsQuery.data.items.map((application) => (
              <ApplicationRow application={application} key={application.tenant_id} />
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center px-6 py-14 text-center">
            <span className="rounded-full bg-mint p-4 text-pine">
              <FileText size={24} />
            </span>
            <h2 className="mt-4 font-semibold text-ink">暂无申请记录</h2>
            <p className="mt-2 max-w-sm text-sm text-slate-500">
              当前筛选条件下没有门店入驻申请。切换状态后可查看其他记录。
            </p>
          </div>
        )}
      </section>

      <div className="flex items-center justify-end gap-2">
        <button
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={page <= 1}
          onClick={() => setPage(page - 1)}
          type="button"
        >
          上一页
        </button>
        <span className="min-w-20 text-center text-sm text-slate-500">
          {page} / {totalPages}
        </span>
        <button
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 disabled:cursor-not-allowed disabled:opacity-40"
          disabled={page >= totalPages}
          onClick={() => setPage(page + 1)}
          type="button"
        >
          下一页
        </button>
      </div>
    </div>
  );
}

function ApplicationRow({ application }: { application: MyTenantApplicationItem }) {
  return (
    <Link
      className="group grid gap-4 px-5 py-5 transition-colors hover:bg-slate-50 sm:grid-cols-[1fr_180px_130px_auto] sm:items-center"
      to={`/institution/store/application/${application.tenant_id}/status`}
    >
      <div>
        <div className="font-medium text-ink">{application.name}</div>
        <div className="mt-1 text-xs text-slate-500">{application.tenant_code}</div>
      </div>
      <div className="text-sm text-slate-600">
        {application.province} / {application.city}
      </div>
      <div>
        <span
          className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${applicationStatusClassName(application.status)}`}
        >
          {applicationStatusLabel(application.status)}
        </span>
      </div>
      <div className="flex items-center justify-between gap-3 text-sm text-slate-500 sm:justify-end">
        <span>{formatDate(application.submitted_at)}</span>
        <ChevronRight className="transition-transform group-hover:translate-x-0.5" size={18} />
      </div>
    </Link>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).format(new Date(value));
}
