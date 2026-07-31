import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Search } from "lucide-react";
import { listTenantReviewQueue } from "../api";
import type { TenantReviewQueueItem } from "../types";

const PAGE_SIZE = 10;

export function TenantReviewListPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = Math.max(Number(searchParams.get("page") ?? "1"), 1);
  const keyword = searchParams.get("keyword") ?? "";
  const province = searchParams.get("province") ?? "";
  const city = searchParams.get("city") ?? "";

  const queueQuery = useQuery({
    queryKey: ["platform", "tenant-review-queue", { page, keyword, province, city }],
    queryFn: () =>
      listTenantReviewQueue({
        page,
        page_size: PAGE_SIZE,
        keyword,
        province,
        city
      })
  });

  const total = queueQuery.data?.total ?? 0;
  const totalPages = Math.max(Math.ceil(total / PAGE_SIZE), 1);

  function updateFilters(formData: FormData) {
    const next = new URLSearchParams();
    const nextKeyword = String(formData.get("keyword") ?? "").trim();
    const nextProvince = String(formData.get("province") ?? "").trim();
    const nextCity = String(formData.get("city") ?? "").trim();
    if (nextKeyword) next.set("keyword", nextKeyword);
    if (nextProvince) next.set("province", nextProvince);
    if (nextCity) next.set("city", nextCity);
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
      <div>
        <p className="text-sm font-medium text-pine">Store Review</p>
        <h1 className="mt-1 text-2xl font-semibold text-ink">门店入驻审核列表</h1>
        <p className="mt-2 text-sm text-slate-500">仅展示 status=pending 的入驻申请，区域范围由后端权限控制。</p>
      </div>

      <form
        action={(formData) => updateFilters(formData)}
        className="grid gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm md:grid-cols-[1fr_160px_160px_auto]"
      >
        <label className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
          <input
            className="h-10 w-full rounded-md border border-slate-200 pl-9 pr-3 text-sm outline-none focus:border-pine"
            defaultValue={keyword}
            name="keyword"
            placeholder="搜索门店名称/编码"
          />
        </label>
        <input
          className="h-10 rounded-md border border-slate-200 px-3 text-sm outline-none focus:border-pine"
          defaultValue={province}
          name="province"
          placeholder="省份"
        />
        <input
          className="h-10 rounded-md border border-slate-200 px-3 text-sm outline-none focus:border-pine"
          defaultValue={city}
          name="city"
          placeholder="城市"
        />
        <button className="h-10 rounded-md bg-pine px-4 text-sm font-medium text-white hover:bg-pine/90" type="submit">
          查询
        </button>
      </form>

      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <span className="text-sm text-slate-500">共 {total} 条待审核申请</span>
          {queueQuery.isFetching ? <span className="text-xs text-slate-400">刷新中...</span> : null}
        </div>

        {queueQuery.isLoading ? (
          <div className="p-6 text-sm text-slate-500">正在加载列表...</div>
        ) : queueQuery.isError ? (
          <div className="p-6 text-sm text-red-700">列表加载失败，请检查 API 服务或当前账号权限。</div>
        ) : queueQuery.data?.items.length ? (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-100 text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">门店</th>
                  <th className="px-4 py-3 font-medium">区域</th>
                  <th className="px-4 py-3 font-medium">联系人</th>
                  <th className="px-4 py-3 font-medium">附件</th>
                  <th className="px-4 py-3 font-medium">提交时间</th>
                  <th className="px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {queueQuery.data.items.map((item) => (
                  <ReviewRow item={item} key={item.tenant_id} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="p-6 text-sm text-slate-500">暂无待审核申请。</div>
        )}
      </div>

      <div className="flex items-center justify-end gap-2">
        <button
          className="rounded-md border border-slate-200 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
          disabled={page <= 1}
          onClick={() => setPage(page - 1)}
          type="button"
        >
          上一页
        </button>
        <span className="text-sm text-slate-500">
          {page} / {totalPages}
        </span>
        <button
          className="rounded-md border border-slate-200 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-40"
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

function ReviewRow({ item }: { item: TenantReviewQueueItem }) {
  return (
    <tr className="hover:bg-slate-50">
      <td className="px-4 py-3">
        <div className="font-medium text-ink">{item.name}</div>
        <div className="mt-1 text-xs text-slate-500">{item.tenant_code}</div>
      </td>
      <td className="px-4 py-3 text-slate-600">
        {item.province} / {item.city}
        {item.district ? ` / ${item.district}` : ""}
      </td>
      <td className="px-4 py-3 text-slate-600">
        <div>{item.contact_name ?? "-"}</div>
        <div className="mt-1 text-xs text-slate-500">{item.contact_phone ?? "-"}</div>
      </td>
      <td className="px-4 py-3 text-slate-600">{item.attachment_count}</td>
      <td className="px-4 py-3 text-slate-600">{formatDateTime(item.submitted_at)}</td>
      <td className="px-4 py-3">
        <Link className="rounded-md bg-pine px-3 py-2 text-xs font-medium text-white hover:bg-pine/90" to={`${item.tenant_id}`}>
          查看审核
        </Link>
      </td>
    </tr>
  );
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}
