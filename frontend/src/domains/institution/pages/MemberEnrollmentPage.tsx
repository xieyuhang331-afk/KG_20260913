import { RefreshCw, UserRound } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listMemberEnrollments } from "../api";
import type { MemberEnrollmentSummary } from "../types";
import { getSafeApiError } from "@/shared/api/slice3";
import { EmptyPanel, Feedback, LoadingPanel, secondaryButtonClassName } from "../受控入驻界面";

export function MemberEnrollmentPage() {
  const [items, setItems] = useState<MemberEnrollmentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [cursor, setCursor] = useState<string | undefined>();
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<string | undefined>>([]);

  const load = useCallback(
    async (targetCursor?: string, targetStatus = status) => {
      setLoading(true);
      try {
        const page = await listMemberEnrollments({
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
          <p className="text-xs font-semibold tracking-wide text-teal-700">会员服务 / 服务准备</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">会员入组</h1>
          <p className="mt-2 text-sm text-slate-500">沿邀请、实名、同意、分配和服务准备中案例推进。</p>
        </div>
        <button className={secondaryButtonClassName} disabled={loading} onClick={() => void load(cursor)} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      <Feedback message={error} tone="error" />
      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="font-semibold">当前机构会员</h2>
            <p className="mt-1 text-xs text-slate-500">上一批 / 下一批按 opaque cursor 获取，不显示总页数。</p>
          </div>
          <select
            aria-label="入组状态"
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
            onChange={(event) => void filter(event.target.value)}
            value={status}
          >
            <option value="">全部状态</option>
            <option value="ACCEPTED">已接受邀请</option>
            <option value="IDENTITY_SUBMITTED">待机构核验</option>
            <option value="INSTITUTION_CHECKED">待平台终审</option>
            <option value="IDENTITY_APPROVED">待分配</option>
            <option value="ASSIGNMENT_PENDING">待健管师接受</option>
            <option value="CASE_PREPARING">服务准备中</option>
          </select>
        </div>
        {loading ? (
          <div className="p-5">
            <LoadingPanel label="正在加载会员入组…" />
          </div>
        ) : items.length === 0 ? (
          <div className="p-5">
            <EmptyPanel title="当前批次没有会员" description="先创建会员邀请，或调整状态筛选后重试。" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>
                  <th className="px-5 py-3">入组标识</th>
                  <th className="px-5 py-3">模式</th>
                  <th className="px-5 py-3">旅程状态</th>
                  <th className="px-5 py-3">最近节点</th>
                  <th className="px-5 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((item) => (
                  <tr key={item.enrollment_id}>
                    <td className="px-5 py-4">
                      <span className="inline-flex items-center gap-2 font-mono text-xs">
                        <UserRound aria-hidden="true" size={15} />
                        {compactId(item.enrollment_id)}
                      </span>
                    </td>
                    <td className="px-5 py-4">{item.mode === "SELF" ? "本人" : "代办老人"}</td>
                    <td className="px-5 py-4">
                      <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-semibold text-blue-700">
                        {statusLabel(item.status)}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-slate-500">
                      {formatTime(item.case_created_at ?? item.identity_verified_at ?? item.accepted_at)}
                    </td>
                    <td className="px-5 py-4 text-right">
                      <Link
                        className={secondaryButtonClassName}
                        to={`/institution/member-enrollments/${item.enrollment_id}`}
                      >
                        查看并推进
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
            className={secondaryButtonClassName}
            disabled={loading || history.length === 0}
            onClick={() => void previous()}
            type="button"
          >
            上一批
          </button>
          <button
            className={secondaryButtonClassName}
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
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}
function formatTime(value: string | null) {
  if (!value) return "尚未进入下一节点";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待确认"
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(date);
}
function statusLabel(value: string) {
  return (
    (
      {
        ACCEPTED: "已接受邀请",
        IDENTITY_SUBMITTED: "待机构核验",
        INSTITUTION_CHECKED: "待平台终审",
        IDENTITY_APPROVED: "待分配健管师",
        ASSIGNMENT_PENDING: "待健管师接受",
        CASE_PREPARING: "服务准备中",
      } as Record<string, string>
    )[value] ?? value
  );
}
