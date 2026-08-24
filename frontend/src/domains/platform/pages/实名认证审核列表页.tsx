import { BadgeCheck, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listMemberIdentityReviews } from "../实名认证审核接口";
import type { MemberIdentityReviewSummary } from "../实名认证审核类型";
import { getSafeApiError } from "@/shared/api/slice3";

export function IdentityReviewListPage() {
  const [items, setItems] = useState<MemberIdentityReviewSummary[]>([]);
  const [status, setStatus] = useState("INSTITUTION_CHECKED");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<string | undefined>>([]);

  const load = useCallback(
    async (targetCursor?: string, targetStatus = status) => {
      setLoading(true);
      try {
        const page = await listMemberIdentityReviews({
          cursor: targetCursor,
          limit: 20,
          status: targetStatus || undefined,
        });
        setItems(page.items);
        setNextCursor(page.next_cursor ?? null);
        setError("");
      } catch (reason) {
        setError(getSafeApiError(reason).message);
      } finally {
        setLoading(false);
      }
    },
    [status],
  );

  useEffect(() => void load(), [load]);

  async function filter(value: string) {
    setStatus(value);
    setCursor(undefined);
    setHistory([]);
    await load(undefined, value);
  }
  async function next() {
    if (!nextCursor) return;
    setHistory((value) => [...value, cursor]);
    setCursor(nextCursor);
    await load(nextCursor);
  }
  async function previous() {
    const value = history[history.length - 1];
    setHistory((current) => current.slice(0, -1));
    setCursor(value);
    await load(value);
  }

  return (
    <main className="mx-auto w-full max-w-[1280px] space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">用户治理 / 身份终审</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">用户实名认证审核</h1>
          <p className="mt-2 text-sm text-slate-500">仅显示脱敏身份摘要；领取后按当前实名材料版本终审。</p>
        </div>
        <button
          className="inline-flex items-center rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700"
          disabled={loading}
          onClick={() => void load(cursor)}
          type="button"
        >
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      {error ? (
        <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800" role="alert">
          <p>{error}</p>
          <button className="mt-3 font-semibold underline" onClick={() => void load(cursor)} type="button">
            重新加载
          </button>
        </section>
      ) : null}
      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="font-semibold">审核队列</h2>
            <p className="mt-1 text-xs text-slate-500">按批次加载审核记录，不推算总页数。</p>
          </div>
          <select
            aria-label="审核状态"
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
            onChange={(event) => void filter(event.target.value)}
            value={status}
          >
            <option value="INSTITUTION_CHECKED">待平台终审</option>
            <option value="CLAIMED">我已领取</option>
            <option value="NEEDS_CORRECTION">待补正</option>
            <option value="APPROVED">已通过</option>
            <option value="REJECTED">已驳回</option>
            <option value="">全部状态</option>
          </select>
        </div>
        {loading ? (
          <div className="p-8 text-center text-sm text-slate-500">正在加载审核队列…</div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center">
            <BadgeCheck aria-hidden="true" className="mx-auto text-slate-300" size={28} />
            <h3 className="mt-3 font-semibold">暂无待审核实名认证</h3>
            <p className="mt-1 text-sm text-slate-500">调整状态筛选，或稍后刷新。</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>
                  <th className="px-5 py-3">审核标识</th>
                  <th className="px-5 py-3">入组模式</th>
                  <th className="px-5 py-3">脱敏证件</th>
                  <th className="px-5 py-3">状态</th>
                  <th className="px-5 py-3">提交时间</th>
                  <th className="px-5 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((item) => (
                  <tr key={item.review_id}>
                    <td className="px-5 py-4 font-mono text-xs">{compactId(item.review_id)}</td>
                    <td className="px-5 py-4">{item.mode === "SELF" ? "本人" : "代办老人"}</td>
                    <td className="px-5 py-4 font-medium">{item.id_masked}</td>
                    <td className="px-5 py-4">
                      <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">
                        {reviewStatusLabel(item.status)}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-slate-500">{formatTime(item.submitted_at)}</td>
                    <td className="px-5 py-4 text-right">
                      <Link
                        className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold text-slate-700"
                        to={`/platform/identity-reviews/${item.review_id}`}
                      >
                        查看详情
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">
          <button
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold disabled:opacity-50"
            disabled={loading || history.length === 0}
            onClick={() => void previous()}
            type="button"
          >
            上一批
          </button>
          <button
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold disabled:opacity-50"
            disabled={loading || !nextCursor}
            onClick={() => void next()}
            type="button"
          >
            下一批
          </button>
        </div>
      </section>
    </main>
  );
}

function compactId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}
function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待确认"
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(date);
}
function reviewStatusLabel(status: string) {
  return (
    {
      INSTITUTION_CHECKED: "机构核验通过，待平台终审",
      CLAIMED: "审核员已领取",
      PLATFORM_REVIEWING: "平台审核中",
      NEEDS_CORRECTION: "待用户补正",
      VERIFIED: "审核已通过",
      APPROVED: "审核已通过",
      REJECTED: "审核未通过",
    }[status] ?? "状态待确认"
  );
}
