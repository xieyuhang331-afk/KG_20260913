import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowRight, Building2, ClipboardCheck, ShieldCheck, UsersRound } from "lucide-react";
import { listTenantReviewQueue } from "../api";
import { useAuthStore } from "@/shared/auth/authStore";

export function PlatformHomePage() {
  const { currentUser } = useAuthStore();
  const queueQuery = useQuery({
    queryKey: ["platform", "tenant-review-queue", "home"],
    queryFn: () => listTenantReviewQueue({ page: 1, page_size: 5 }),
  });

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-sm font-medium text-pine">Platform Web</p>
            <h1 className="mt-2 text-2xl font-semibold text-ink">平台审核工作台</h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-600">
              聚焦门店入驻审核闭环，先把待审核、详情查看、通过与驳回跑顺。
            </p>
          </div>
          <div className="rounded-md bg-slate-50 px-4 py-3 text-sm text-slate-600">
            当前角色：
            <span className="font-semibold text-ink">
              {currentUser?.role === "super_admin" ? "平台管理员" : (currentUser?.role ?? "-")}
            </span>
          </div>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <MetricCard
          icon={<ClipboardCheck size={20} />}
          label="待审核申请"
          loading={queueQuery.isLoading}
          value={queueQuery.data?.total ?? 0}
        />
        <MetricCard icon={<Building2 size={20} />} label="审核工作流" value="3 类" />
        <MetricCard icon={<ShieldCheck size={20} />} label="访问边界" value="按角色隔离" />
      </section>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.55fr)_minmax(300px,0.45fr)]">
        <section className="min-w-0 rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-ink">近期待审核</h2>
              <p className="text-sm text-slate-500">展示入驻审核队列的前 5 条。</p>
            </div>
            <Link
              className="inline-flex items-center gap-2 rounded-md bg-pine px-3 py-2 text-sm font-medium text-white hover:bg-pine/90"
              to="/platform/stores/reviews"
            >
              进入审核列表
              <ArrowRight size={16} />
            </Link>
          </div>

          {queueQuery.isLoading ? (
            <div className="rounded-md bg-slate-50 p-4 text-sm text-slate-500">正在加载审核队列...</div>
          ) : queueQuery.isError ? (
            <div className="rounded-md bg-red-50 p-4 text-sm text-red-700">
              审核队列加载失败，请检查登录状态或 API 服务。
            </div>
          ) : queueQuery.data?.items.length ? (
            <div className="divide-y divide-slate-100">
              {queueQuery.data.items.map((item) => (
                <Link
                  className="flex items-center justify-between gap-4 py-3 hover:bg-slate-50"
                  key={item.tenant_id}
                  to={`/platform/stores/reviews/${item.tenant_id}`}
                >
                  <div>
                    <p className="font-medium text-ink">{item.name}</p>
                    <p className="mt-1 text-sm text-slate-500">
                      {item.province} / {item.city} · {item.tenant_code}
                    </p>
                  </div>
                  <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-medium text-amber-700">待审核</span>
                </Link>
              ))}
            </div>
          ) : (
            <div className="rounded-md bg-slate-50 p-4 text-sm text-slate-500">暂无待审核申请。</div>
          )}
        </section>
        <aside className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center gap-3">
            <span className="rounded-lg bg-teal-50 p-2 text-teal-700">
              <UsersRound aria-hidden="true" size={20} />
            </span>
            <div>
              <h2 className="font-semibold text-ink">今日判断</h2>
              <p className="mt-1 text-xs text-slate-500">按业务对象进入对应工作台。</p>
            </div>
          </div>
          <nav aria-label="平台审核快捷入口" className="mt-5 grid gap-3">
            <WorkbenchLink description="核对机构材料与入驻资格" label="门店入驻审核" to="/platform/stores/reviews" />
            <WorkbenchLink
              description="处理受控入驻与补正任务"
              label="受控入驻审核"
              to="/platform/institution-reviews"
            />
            <WorkbenchLink description="领取并处理会员实名审核" label="会员实名审核" to="/platform/identity-reviews" />
          </nav>
        </aside>
      </div>
    </div>
  );
}

function WorkbenchLink({ description, label, to }: { description: string; label: string; to: string }) {
  return (
    <Link
      className="group flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-4 py-3 transition hover:border-teal-300 hover:bg-teal-50/60"
      to={to}
    >
      <span>
        <span className="block text-sm font-semibold text-slate-900">{label}</span>
        <span className="mt-1 block text-xs text-slate-500">{description}</span>
      </span>
      <ArrowRight
        aria-hidden="true"
        className="shrink-0 text-slate-400 transition group-hover:text-teal-700"
        size={16}
      />
    </Link>
  );
}

function MetricCard({
  icon,
  label,
  value,
  loading = false,
}: {
  icon: ReactNode;
  label: string;
  value: string | number;
  loading?: boolean;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-3 text-pine">
        {icon}
        <span className="text-sm font-medium text-slate-500">{label}</span>
      </div>
      <div className="mt-4 text-2xl font-semibold text-ink">{loading ? "-" : value}</div>
    </div>
  );
}
