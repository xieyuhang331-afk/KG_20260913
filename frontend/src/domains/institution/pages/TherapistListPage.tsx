import { AlertTriangle, RefreshCw, UserRoundSearch } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { isApiError } from "@/shared/api/errors";
import { EmptyPanel, Feedback, LoadingPanel, secondaryButtonClassName } from "../受控入驻界面";
import { getTherapist, listTherapists } from "../api";
import type { TherapistProfile, TherapistQualification, TherapistStatus } from "../types";

type Detail = { profile: TherapistProfile; qualifications: TherapistQualification[] };

export function TherapistListPage() {
  const [items, setItems] = useState<TherapistProfile[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState<TherapistStatus | "">("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);

  const load = useCallback(
    async (targetCursor?: string, targetStatus: TherapistStatus | "" = status) => {
      setLoading(true);
      setError("");
      setDetail(null);
      try {
        const value = await listTherapists({ status: targetStatus || undefined, cursor: targetCursor, limit: 20 });
        setItems(value.items);
        setNextCursor(value.next_cursor ?? null);
      } catch (loadError) {
        setError(listErrorMessage(loadError));
      } finally {
        setLoading(false);
      }
    },
    [status],
  );

  useEffect(() => {
    void load();
  }, [load]);

  async function openDetail(item: TherapistProfile) {
    setDetailLoading(true);
    setError("");
    try {
      setDetail(await getTherapist(item.therapist_id));
    } catch (loadError) {
      setError(listErrorMessage(loadError));
    } finally {
      setDetailLoading(false);
    }
  }

  async function changeStatus(value: TherapistStatus | "") {
    setStatus(value);
    setCursor(undefined);
    setHistory([]);
    await load(undefined, value);
  }

  async function nextPage() {
    if (!nextCursor) return;
    setHistory((current) => [...current, cursor]);
    setCursor(nextCursor);
    await load(nextCursor);
  }

  async function previousPage() {
    const previous = history[history.length - 1];
    setHistory((current) => current.slice(0, -1));
    setCursor(previous);
    await load(previous);
  }

  return (
    <main className="mx-auto w-full max-w-[1280px] space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">人员管理 / 本机构</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">健管师团队</h1>
          <p className="mt-2 text-sm text-slate-500">查看当前机构健管师的资质、工作状态和服务容量。</p>
        </div>
        <button className={secondaryButtonClassName} disabled={loading} onClick={() => void load(cursor)} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      <Feedback message={error} tone="error" />
      <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-panel">
        <label className="text-sm font-medium text-slate-700">
          工作状态
          <select
            className="ml-3 min-h-10 rounded-lg border border-slate-200 bg-white px-3"
            onChange={(event) => void changeStatus(event.target.value as TherapistStatus | "")}
            value={status}
          >
            <option value="">全部状态</option>
            <option value="APPROVED_ACTIVE">服务中</option>
            <option value="SUSPENDED">已暂停</option>
            <option value="UNDER_REVIEW">审核中</option>
            <option value="EXITED">已退出</option>
          </select>
        </label>
      </section>
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(340px,0.75fr)]">
        <section className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
          {loading ? (
            <div className="p-5">
              <LoadingPanel label="正在加载健管师…" />
            </div>
          ) : error ? (
            <div className="p-5">
              <EmptyPanel
                title="健管师团队暂时不可用"
                description={error}
                action={
                  <button className={secondaryButtonClassName} onClick={() => void load()} type="button">
                    重试
                  </button>
                }
              />
            </div>
          ) : items.length === 0 ? (
            <div className="p-5">
              <EmptyPanel title="还没有健管师" description="先创建邀请；对方完成激活和资质审核后会出现在这里。" />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-[760px] w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs font-semibold text-slate-500">
                  <tr>
                    <th className="px-4 py-3">健管师</th>
                    <th className="px-4 py-3">状态</th>
                    <th className="px-4 py-3">资质有效期</th>
                    <th className="px-4 py-3">当前负载</th>
                    <th className="px-4 py-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {items.map((item) => (
                    <tr key={item.therapist_id}>
                      <td className="px-4 py-4">
                        <p className="font-semibold text-slate-950">{item.display_name || "资料待完善"}</p>
                        <p className="mt-1 text-xs text-slate-500">
                          {(item.service_tags ?? []).map(tagLabel).join("、") || "服务标签待完善"}
                        </p>
                      </td>
                      <td className="px-4 py-4">
                        <ProfileStatus status={item.status} />
                      </td>
                      <td className="px-4 py-4">
                        <p>{item.qualification_valid_until ?? "待审核"}</p>
                        {expiryRisk(item.qualification_valid_until) ? (
                          <span className="mt-1 inline-flex items-center text-xs font-semibold text-amber-700">
                            <AlertTriangle aria-hidden="true" className="mr-1" size={13} />
                            即将到期
                          </span>
                        ) : null}
                      </td>
                      <td className="px-4 py-4 font-mono font-semibold">
                        {item.active_case_count} / {item.capacity_limit}
                      </td>
                      <td className="px-4 py-4 text-right">
                        <button
                          aria-label={`查看${item.display_name || "健管师"}详情`}
                          className={secondaryButtonClassName}
                          onClick={() => void openDetail(item)}
                          type="button"
                        >
                          查看详情
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex justify-end gap-2 border-t border-slate-100 px-4 py-4">
            <button
              className={secondaryButtonClassName}
              disabled={loading || history.length === 0}
              onClick={() => void previousPage()}
              type="button"
            >
              上一批
            </button>
            <button
              className={secondaryButtonClassName}
              disabled={loading || !nextCursor}
              onClick={() => void nextPage()}
              type="button"
            >
              下一批
            </button>
          </div>
        </section>
        <section className="min-w-0">
          {detailLoading ? (
            <LoadingPanel label="正在读取资质详情…" />
          ) : detail ? (
            <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
              <div>
                <p className="text-xs font-semibold text-teal-700">健管师详情</p>
                <h2 className="mt-1 text-xl font-semibold text-slate-950">{detail.profile.display_name}</h2>
                <p className="mt-2 text-sm leading-6 text-slate-500">{detail.profile.practice_summary}</p>
              </div>
              <dl className="grid grid-cols-2 gap-3 rounded-lg bg-slate-50 p-4 text-sm">
                <div>
                  <dt className="text-xs text-slate-500">工作状态</dt>
                  <dd className="mt-1 font-semibold">{statusLabel(detail.profile.status)}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">当前负载</dt>
                  <dd className="mt-1 font-semibold">
                    {detail.profile.active_case_count} / {detail.profile.capacity_limit}
                  </dd>
                </div>
              </dl>
              <div>
                <h3 className="font-semibold text-slate-950">资质摘要</h3>
                {detail.qualifications.length ? (
                  <ul className="mt-3 space-y-3">
                    {detail.qualifications.map((item) => (
                      <li className="rounded-lg border border-slate-200 p-3" key={item.qualification_version_id}>
                        <div className="flex items-center justify-between gap-2">
                          <strong className="text-sm">代谢健康执业资质</strong>
                          <span className="text-xs text-slate-500">v{item.version_no}</span>
                        </div>
                        <p className="mt-2 font-mono text-sm">{item.masked_certificate_no}</p>
                        <p className="mt-1 text-xs text-slate-500">
                          {item.valid_from} 至 {item.valid_until}
                        </p>
                        <p className="mt-1 text-xs text-slate-500">材料 {item.attachment_count} 份</p>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-sm text-slate-500">尚无资质版本。</p>
                )}
              </div>
            </div>
          ) : (
            <EmptyPanel
              action={<UserRoundSearch aria-hidden="true" className="mx-auto text-slate-400" size={28} />}
              title="选择健管师"
              description="从列表选择一名健管师，查看脱敏资质摘要和容量。"
            />
          )}
        </section>
      </div>
    </main>
  );
}

function listErrorMessage(error: unknown) {
  if (!isApiError(error)) return "加载失败，请重试。";
  if (error.status === 401) return "登录状态已失效，请重新登录。";
  if (error.status === 403) return "当前账号无权查看该机构的健管师。";
  if (error.status === 404) return "记录不存在或已不在当前权限范围内。";
  if (error.status === 503) return "服务暂时不可用，请稍后重试。";
  return "加载失败，请重试。";
}
function expiryRisk(value: string | null) {
  if (!value) return false;
  const expiry = new Date(`${value}T00:00:00+08:00`).getTime();
  const now = Date.now();
  return expiry >= now && expiry - now <= 60 * 24 * 60 * 60 * 1000;
}
function tagLabel(value: string) {
  return (
    (
      {
        HYPERTENSION: "血压管理",
        GLUCOSE_METABOLISM: "血糖代谢",
        DYSLIPIDEMIA: "血脂管理",
        OBESITY: "体重管理",
      } as Record<string, string>
    )[value] ?? value
  );
}
function statusLabel(value: TherapistStatus) {
  return (
    {
      ACTIVATED: "待完善资料",
      DRAFT: "草稿",
      SUBMITTED: "待审核",
      UNDER_REVIEW: "审核中",
      NEEDS_CORRECTION: "待补正",
      RESUBMITTED: "已重提",
      APPROVED_ACTIVE: "服务中",
      SUSPENDED: "已暂停",
      EXITED: "已退出",
      REJECTED: "已拒绝",
    } as Record<TherapistStatus, string>
  )[value];
}
function ProfileStatus({ status }: { status: TherapistStatus }) {
  const danger = status === "SUSPENDED" || status === "REJECTED" || status === "EXITED";
  const active = status === "APPROVED_ACTIVE";
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${active ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : danger ? "bg-red-50 text-red-700 ring-red-200" : "bg-blue-50 text-blue-700 ring-blue-200"}`}
    >
      {statusLabel(status)}
    </span>
  );
}
