import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, Clock3, X } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { getTenantApplicationDetail } from "../api";
import { applicationStatusClassName, applicationStatusLabel } from "../status";
import type { TenantApplicationStatus } from "../types";
import { isApiError } from "@/shared/api/errors";

export function ApplicationDetailPage() {
  const { tenantId } = useParams();
  const numericTenantId = Number(tenantId);
  const detailQuery = useQuery({
    queryKey: ["institution", "application-detail", numericTenantId],
    queryFn: () => getTenantApplicationDetail(numericTenantId),
    enabled: Number.isFinite(numericTenantId) && numericTenantId > 0
  });

  if (!Number.isFinite(numericTenantId) || numericTenantId <= 0) {
    return <MessageCard className="border-red-200 bg-red-50 text-red-700">无效的申请编号。</MessageCard>;
  }

  if (detailQuery.isLoading) {
    return <MessageCard>正在加载申请详情...</MessageCard>;
  }

  if (detailQuery.isError) {
    return <MessageCard className="border-red-200 bg-red-50 text-red-700">{getErrorText(detailQuery.error)}</MessageCard>;
  }

  const detail = detailQuery.data;
  if (!detail) return null;

  return (
    <div className="space-y-5">
      <Link
        className="inline-flex items-center gap-2 text-sm font-medium text-pine hover:text-pine/80"
        to="/institution/store/applications"
      >
        <ArrowLeft size={16} />
        返回我的申请
      </Link>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-pine">Application Detail</p>
            <h1 className="mt-2 text-2xl font-semibold text-ink">{detail.name}</h1>
            <p className="mt-2 text-sm text-slate-500">{detail.tenant_code}</p>
          </div>
          <span
            className={`inline-flex self-start rounded-full px-3 py-1.5 text-sm font-medium ring-1 ring-inset ${applicationStatusClassName(detail.status)}`}
          >
            {applicationStatusLabel(detail.status)}
          </span>
        </div>
      </section>

      <ApplicationProgress status={detail.status} />

      <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
        <InfoCard title="申请信息">
          <Definition label="申请编号" value={detail.tenant_id} />
          <Definition label="门店编码" value={detail.tenant_code} />
          <Definition label="提交时间" value={formatDateTime(detail.submitted_at)} />
          <Definition label="审核时间" value={detail.reviewed_at ? formatDateTime(detail.reviewed_at) : null} />
          <Definition label="通过时间" value={detail.approved_at ? formatDateTime(detail.approved_at) : null} />
        </InfoCard>

        <section
          className={[
            "rounded-xl border p-5 shadow-sm",
            detail.status === "rejected" ? "border-red-200 bg-red-50" : "border-slate-200 bg-white"
          ].join(" ")}
        >
          <h2 className="font-semibold text-ink">{detail.status === "rejected" ? "驳回说明" : "当前说明"}</h2>
          <p className="mt-3 text-sm leading-6 text-slate-600">{statusDescription(detail.status)}</p>
          {detail.status === "rejected" ? (
            <div className="mt-4 rounded-lg border border-red-200 bg-white/80 p-4">
              <div className="text-xs font-medium text-red-600">驳回原因</div>
              <div className="mt-2 text-sm font-medium text-ink">{detail.reject_reason || "-"}</div>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}

function ApplicationProgress({ status }: { status: TenantApplicationStatus }) {
  const reviewComplete = status === "active" || status === "rejected";

  return (
    <section className="rounded-xl border border-slate-200 bg-white px-5 py-6 shadow-sm">
      <h2 className="font-semibold text-ink">申请状态轨迹</h2>
      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        <ProgressStep icon={<Check size={16} />} label="已提交申请" tone="complete" />
        <ProgressStep
          icon={status === "rejected" ? <X size={16} /> : reviewComplete ? <Check size={16} /> : <Clock3 size={16} />}
          label={status === "rejected" ? "审核未通过" : status === "active" ? "审核已通过" : "平台审核中"}
          tone={status === "rejected" ? "rejected" : reviewComplete ? "complete" : "current"}
        />
        <ProgressStep
          icon={status === "active" ? <Check size={16} /> : <Clock3 size={16} />}
          label={status === "active" ? "门店已开通" : "等待审核结果"}
          tone={status === "active" ? "complete" : "waiting"}
        />
      </div>
    </section>
  );
}

function ProgressStep({
  icon,
  label,
  tone
}: {
  icon: ReactNode;
  label: string;
  tone: "complete" | "current" | "waiting" | "rejected";
}) {
  const toneClass =
    tone === "complete"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : tone === "current"
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : tone === "rejected"
          ? "border-red-200 bg-red-50 text-red-700"
          : "border-slate-200 bg-slate-50 text-slate-400";

  return (
    <div className={`flex items-center gap-3 rounded-lg border px-4 py-3 ${toneClass}`}>
      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-white/80">{icon}</span>
      <span className="text-sm font-medium">{label}</span>
    </div>
  );
}

function InfoCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="mb-4 font-semibold text-ink">{title}</h2>
      <div className="grid gap-3">{children}</div>
    </section>
  );
}

function Definition({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="grid gap-1 text-sm sm:grid-cols-[120px_1fr]">
      <span className="text-slate-500">{label}</span>
      <span className="font-medium text-ink">{value || "-"}</span>
    </div>
  );
}

function MessageCard({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500 ${className}`}>{children}</div>;
}

function statusDescription(status: TenantApplicationStatus) {
  if (status === "pending") return "平台正在审核申请资料。审核结果更新后，会在此页面显示。";
  if (status === "active") return "申请已通过审核，门店入驻状态已开通。";
  return "申请未通过本次审核，请根据驳回原因核对资料。后续处理方式以平台通知为准。";
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function getErrorText(error: unknown) {
  if (isApiError(error)) return error.message;
  if (error instanceof Error) return error.message;
  return "申请详情加载失败，请稍后重试。";
}
