import type { FormEvent, ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, CheckCircle2, XCircle } from "lucide-react";
import { approveTenantReview, getTenantReviewDetail, rejectTenantReview } from "../api";
import { isApiError } from "@/shared/api/errors";

export function TenantReviewDetailPage() {
  const { tenantId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const numericTenantId = Number(tenantId);

  const detailQuery = useQuery({
    queryKey: ["platform", "tenant-review-detail", numericTenantId],
    queryFn: () => getTenantReviewDetail(numericTenantId),
    enabled: Number.isFinite(numericTenantId) && numericTenantId > 0
  });

  const approveMutation = useMutation({
    mutationFn: () => approveTenantReview(numericTenantId, {}),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["platform", "tenant-review-queue"] });
      navigate("/platform/stores/reviews");
    }
  });

  const rejectMutation = useMutation({
    mutationFn: (reason: string) => rejectTenantReview(numericTenantId, { reason }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["platform", "tenant-review-queue"] });
      navigate("/platform/stores/reviews");
    }
  });

  if (!Number.isFinite(numericTenantId) || numericTenantId <= 0) {
    return <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">无效的审核对象。</div>;
  }

  if (detailQuery.isLoading) {
    return <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-500">正在加载审核详情...</div>;
  }

  if (detailQuery.isError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        {getErrorText(detailQuery.error)}
      </div>
    );
  }

  const detail = detailQuery.data;
  if (!detail) return null;
  const canReview = detail.status.current === "pending";
  const mutationError = approveMutation.error ?? rejectMutation.error;

  return (
    <div className="space-y-5">
      <Link className="inline-flex items-center gap-2 text-sm font-medium text-pine" to="/platform/stores/reviews">
        <ArrowLeft size={16} />
        返回审核列表
      </Link>

      <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <p className="text-sm font-medium text-pine">Tenant Review Detail</p>
            <h1 className="mt-1 text-2xl font-semibold text-ink">{detail.tenant.name}</h1>
            <p className="mt-2 text-sm text-slate-500">
              {detail.tenant.province} / {detail.tenant.city}
              {detail.tenant.district ? ` / ${detail.tenant.district}` : ""} · {detail.tenant.tenant_code}
            </p>
          </div>
          <StatusBadge status={detail.status.current} />
        </div>
      </section>

      <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
        <div className="space-y-5">
          <InfoCard title="门店基础信息">
            <Definition label="门店类型" value={detail.tenant.type} />
            <Definition label="统一社会信用代码" value={detail.tenant.credit_code} />
            <Definition label="许可证号" value={detail.tenant.license_no} />
            <Definition label="法人" value={detail.tenant.legal_person_name} />
            <Definition label="地址" value={detail.tenant.address} />
            <Definition label="定级" value={detail.tenant.grade} />
          </InfoCard>

          <InfoCard title="联系信息">
            <Definition label="联系人" value={detail.contact.contact_name} />
            <Definition label="联系电话" value={detail.contact.contact_phone} />
            <Definition label="联系邮箱" value={detail.contact.contact_email} />
          </InfoCard>

          <InfoCard title={`附件信息（${detail.attachments.length}）`}>
            {detail.attachments.length ? (
              <div className="space-y-3">
                {detail.attachments.map((attachment) => (
                  <a
                    className="block rounded-md border border-slate-200 px-3 py-2 text-sm text-pine hover:bg-slate-50"
                    href={attachment.file_url}
                    key={attachment.id}
                    rel="noreferrer"
                    target="_blank"
                  >
                    {attachment.file_type} · {formatDateTime(attachment.created_at)}
                  </a>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-500">暂无附件。</p>
            )}
          </InfoCard>
        </div>

        <aside className="space-y-5">
          <InfoCard title="审核状态">
            <Definition label="当前状态" value={statusLabel(detail.status.current)} />
            <Definition label="提交时间" value={formatDateTime(detail.submitted_at)} />
            <Definition label="审核时间" value={detail.status.reviewed_at ? formatDateTime(detail.status.reviewed_at) : null} />
            <Definition label="驳回原因" value={detail.status.reject_reason} />
          </InfoCard>

          <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-lg font-semibold text-ink">审核操作</h2>
            {canReview ? (
              <ReviewActions
                approveDisabled={approveMutation.isPending || rejectMutation.isPending}
                onApprove={() => approveMutation.mutate()}
                onReject={(reason) => rejectMutation.mutate(reason)}
                rejectDisabled={approveMutation.isPending || rejectMutation.isPending}
              />
            ) : (
              <p className="mt-3 rounded-md bg-slate-50 p-3 text-sm text-slate-500">该申请已完成审核，不能重复操作。</p>
            )}
            {mutationError ? <p className="mt-3 text-sm text-red-700">{getErrorText(mutationError)}</p> : null}
          </section>
        </aside>
      </div>
    </div>
  );
}

function ReviewActions({
  approveDisabled,
  rejectDisabled,
  onApprove,
  onReject
}: {
  approveDisabled: boolean;
  rejectDisabled: boolean;
  onApprove: () => void;
  onReject: (reason: string) => void;
}) {
  return (
    <div className="mt-4 space-y-4">
      <button
        className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-pine px-4 py-2 text-sm font-medium text-white hover:bg-pine/90 disabled:cursor-not-allowed disabled:opacity-60"
        disabled={approveDisabled}
        onClick={onApprove}
        type="button"
      >
        <CheckCircle2 size={16} />
        审核通过
      </button>

      <form
        onSubmit={(event: FormEvent<HTMLFormElement>) => {
          event.preventDefault();
          const formData = new FormData(event.currentTarget);
          const reason = String(formData.get("reason") ?? "").trim();
          if (reason) onReject(reason);
        }}
        className="space-y-2"
      >
        <textarea
          className="min-h-24 w-full rounded-md border border-slate-200 px-3 py-2 text-sm outline-none focus:border-coral"
          maxLength={500}
          name="reason"
          placeholder="填写驳回原因"
          required
        />
        <button
          className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-coral px-4 py-2 text-sm font-medium text-coral hover:bg-coral/5 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={rejectDisabled}
          type="submit"
        >
          <XCircle size={16} />
          驳回申请
        </button>
      </form>
    </div>
  );
}

function InfoCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="mb-4 text-lg font-semibold text-ink">{title}</h2>
      <div className="grid gap-3">{children}</div>
    </section>
  );
}

function Definition({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="grid gap-1 text-sm md:grid-cols-[140px_1fr]">
      <span className="text-slate-500">{label}</span>
      <span className="font-medium text-ink">{value || "-"}</span>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const className =
    status === "pending"
      ? "bg-amber-50 text-amber-700"
      : status === "active"
        ? "bg-emerald-50 text-emerald-700"
        : "bg-red-50 text-red-700";
  return <span className={`rounded-full px-3 py-1 text-sm font-medium ${className}`}>{statusLabel(status)}</span>;
}

function statusLabel(status: string) {
  if (status === "pending") return "待审核";
  if (status === "active") return "已通过";
  if (status === "rejected") return "已驳回";
  return status;
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
  return "请求失败，请稍后重试。";
}
