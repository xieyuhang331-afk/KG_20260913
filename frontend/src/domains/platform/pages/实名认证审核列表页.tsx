import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, ChevronRight, RotateCw } from "lucide-react";
import { useEffect } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { isApiError } from "@/shared/api/errors";
import { setCurrentUser } from "@/shared/auth/authStore";
import { clearAccessToken } from "@/shared/auth/tokenStorage";
import { listIdentityReviews } from "../实名认证审核接口";
import type { IdentityReviewQueueItem } from "../实名认证审核类型";

const PAGE_SIZE = 20;

export function IdentityReviewListPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);
  const queueQuery = useQuery({
    queryKey: ["platform", "identity-review-queue", page],
    queryFn: () => listIdentityReviews({ page, page_size: PAGE_SIZE }),
    retry: false,
  });

  useEffect(() => {
    if (isApiError(queueQuery.error) && queueQuery.error.status === 401) {
      clearAccessToken();
      setCurrentUser(null);
      navigate("/platform/login", { replace: true });
    }
  }, [navigate, queueQuery.error]);

  const total = queueQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function setPage(nextPage: number) {
    const next = new URLSearchParams(searchParams);
    next.set("page", String(nextPage));
    setSearchParams(next);
  }

  return (
    <div className="space-y-5">
      <header className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-pine">Identity Review</p>
            <h1 className="mt-2 text-2xl font-semibold text-ink">实名认证审核工作台</h1>
            <p className="mt-2 text-sm text-slate-500">仅展示已提交、等待平台人工审核的实名认证申请。</p>
          </div>
          <span className="inline-flex self-start rounded-full bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-700 ring-1 ring-inset ring-amber-200">
            待审核
          </span>
        </div>
      </header>

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="font-semibold text-ink">待审核队列</h2>
            <p className="mt-1 text-xs text-slate-500">共 {total} 条申请</p>
          </div>
          {queueQuery.isFetching && !queueQuery.isLoading ? (
            <span className="inline-flex items-center gap-2 text-xs text-slate-400">
              <RotateCw className="animate-spin" size={14} />
              刷新中
            </span>
          ) : null}
        </div>

        {queueQuery.isLoading ? (
          <QueueMessage>正在加载待审核实名认证...</QueueMessage>
        ) : queueQuery.isError ? (
          <QueueError error={queueQuery.error} onRetry={() => void queueQuery.refetch()} />
        ) : queueQuery.data?.items.length ? (
          <div className="divide-y divide-slate-100">
            {queueQuery.data.items.map((item) => (
              <IdentityReviewRow item={item} key={`${item.user_id}:${item.submission_version}`} />
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center px-6 py-14 text-center">
            <span className="rounded-full bg-mint p-4 text-pine">
              <BadgeCheck size={24} />
            </span>
            <h2 className="mt-4 font-semibold text-ink">暂无待审核实名认证</h2>
            <p className="mt-2 text-sm text-slate-500">新的已提交申请会出现在这里。</p>
          </div>
        )}
      </section>

      <div className="flex items-center justify-end gap-2">
        <button
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
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
          className="rounded-md border border-slate-200 bg-white px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
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

function IdentityReviewRow({ item }: { item: IdentityReviewQueueItem }) {
  return (
    <Link
      className="group grid gap-4 px-5 py-5 hover:bg-slate-50 sm:grid-cols-[160px_1fr_180px_auto] sm:items-center"
      to={`/platform/identity-reviews/${item.user_id}`}
    >
      <div>
        <div className="font-medium text-ink">用户 #{item.user_id}</div>
        <div className="mt-1 text-xs text-slate-500">版本 {item.submission_version}</div>
      </div>
      <div>
        <div className="text-xs text-slate-500">脱敏身份证号</div>
        <div className="mt-1 font-medium text-ink">{item.id_card_masked}</div>
      </div>
      <div className="text-sm text-slate-500">{formatDateTime(item.submitted_at)}</div>
      <ChevronRight className="text-slate-400 transition-transform group-hover:translate-x-0.5" size={18} />
    </Link>
  );
}

function QueueError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (isApiError(error) && error.status === 401) return <QueueMessage>登录状态已失效，正在安全退出...</QueueMessage>;

  return (
    <div className="px-6 py-10 text-center">
      <p className="text-sm text-red-700">{getSafeErrorMessage(error)}</p>
      <button
        className="mt-4 rounded-md border border-slate-200 px-4 py-2 text-sm font-medium text-ink hover:bg-slate-50"
        onClick={onRetry}
        type="button"
      >
        重试
      </button>
    </div>
  );
}

function QueueMessage({ children }: { children: string }) {
  return <div className="px-6 py-10 text-center text-sm text-slate-500">{children}</div>;
}

function getSafeErrorMessage(error: unknown) {
  if (!isApiError(error)) return "实名认证审核队列加载失败，请稍后重试。";
  if (error.status === 403) return "当前账号没有实名认证审核权限。";
  if (error.status === 422) return "审核队列请求参数无效，请刷新页面后重试。";
  if (error.status === 503) return "实名认证审核服务暂不可用，请稍后重试。";
  return "实名认证审核队列加载失败，请稍后重试。";
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
