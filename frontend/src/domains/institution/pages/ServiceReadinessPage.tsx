import { CheckCircle2, Clock3, RefreshCw, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { isApiError } from "@/shared/api/errors";
import { EmptyPanel, Feedback, LoadingPanel, secondaryButtonClassName } from "../受控入驻界面";
import { getServiceReadiness, getServiceReadinessEvidence } from "../api";
import type { ReadinessReason, ServiceReadiness, ServiceReadinessEvidence } from "../types";

export function ServiceReadinessPage() {
  const [current, setCurrent] = useState<ServiceReadiness | null>(null);
  const [history, setHistory] = useState<ServiceReadinessEvidence[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | undefined>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([]);

  const load = useCallback(async (targetCursor?: string) => {
    setLoading(true);
    setError("");
    try {
      const [readiness, evidence] = await Promise.all([
        getServiceReadiness(),
        getServiceReadinessEvidence(targetCursor ? { cursor: targetCursor, limit: 20 } : { limit: 20 }),
      ]);
      setCurrent(readiness);
      setHistory(evidence.items);
      setNextCursor(evidence.next_cursor ?? null);
    } catch (loadError) {
      setError(readinessError(loadError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function nextPage() {
    if (!nextCursor) return;
    setCursorHistory((value) => [...value, cursor]);
    setCursor(nextCursor);
    await load(nextCursor);
  }
  async function previousPage() {
    const previous = cursorHistory[cursorHistory.length - 1];
    setCursorHistory((value) => value.slice(0, -1));
    setCursor(previous);
    await load(previous);
  }

  return (
    <main className="mx-auto w-full max-w-[1280px] space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">机构资格 / 服务门禁</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">服务就绪状态</h1>
          <p className="mt-2 text-sm text-slate-500">
            结果由服务端依据机构许可证与合格健管师证据计算，页面不能直接修改。
          </p>
        </div>
        <button className={secondaryButtonClassName} disabled={loading} onClick={() => void load(cursor)} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新证据
        </button>
      </header>
      <Feedback message={error} tone="error" />
      {loading ? (
        <LoadingPanel label="正在核对服务就绪证据…" />
      ) : current ? (
        <div className="grid items-start gap-5 xl:grid-cols-[minmax(340px,0.85fr)_minmax(0,1.15fr)]">
          <section
            className={`rounded-2xl border p-6 shadow-panel ${current.readiness_status === "SERVICE_READY" ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="flex items-start gap-3">
                {current.readiness_status === "SERVICE_READY" ? (
                  <CheckCircle2 aria-hidden="true" className="mt-0.5 text-emerald-700" size={26} />
                ) : (
                  <ShieldAlert aria-hidden="true" className="mt-0.5 text-amber-700" size={26} />
                )}
                <div>
                  <p className="text-xs font-semibold tracking-wide text-slate-500">当前状态</p>
                  <h2 className="mt-1 text-xl font-semibold text-slate-950">
                    {current.readiness_status === "SERVICE_READY" ? "服务已就绪" : "服务暂未就绪"}
                  </h2>
                </div>
              </div>
              <span className="rounded-full bg-white px-3 py-1.5 text-sm font-semibold ring-1 ring-slate-200">
                就绪证据 v{current.evidence_version}
              </span>
            </div>
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              <div className="rounded-xl bg-white/80 p-4">
                <p className="text-xs text-slate-500">合格健管师</p>
                <p className="mt-1 text-2xl font-semibold text-slate-950">{current.qualified_therapist_count}</p>
              </div>
              <div className="rounded-xl bg-white/80 p-4">
                <p className="text-xs text-slate-500">最近计算</p>
                <p className="mt-1 text-sm font-semibold text-slate-950">{formatTime(current.computed_at)}</p>
              </div>
            </div>
            {current.reason_codes.length ? (
              <div className="mt-5">
                <h3 className="text-sm font-semibold text-slate-800">尚未就绪的原因</h3>
                <ul className="mt-3 grid gap-2 md:grid-cols-2">
                  {current.reason_codes.map((reason) => (
                    <li className="rounded-lg bg-white px-4 py-3 text-sm text-slate-700" key={reason}>
                      {reasonLabel(reason)}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="mt-5 rounded-lg bg-white px-4 py-3 text-sm text-emerald-800">
                机构许可证、服务范围和合格健管师条件均已满足。
              </p>
            )}
          </section>
          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
            <div className="border-b border-slate-100 px-5 py-4">
              <h2 className="font-semibold text-slate-950">就绪证据历史</h2>
              <p className="mt-1 text-xs text-slate-500">每条记录是服务端生成的不可变计算证据。</p>
            </div>
            {history.length ? (
              <ul className="divide-y divide-slate-100">
                {history.map((item) => (
                  <li
                    className="flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
                    key={item.evidence_version}
                  >
                    <div>
                      <p className="font-semibold text-slate-950">就绪证据 v{item.evidence_version}</p>
                      <p className="mt-1 text-xs text-slate-500">
                        原因：{item.reason_codes.map(reasonLabel).join("、") || "无"}
                      </p>
                    </div>
                    <div className="text-sm text-slate-600">
                      <Clock3 aria-hidden="true" className="mr-1 inline" size={14} />
                      {formatTime(item.computed_at)}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="p-5">
                <EmptyPanel title="暂无历史证据" description="服务端完成首次计算后会在这里显示就绪证据版本。" />
              </div>
            )}
            <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">
              <button
                className={secondaryButtonClassName}
                disabled={cursorHistory.length === 0}
                onClick={() => void previousPage()}
                type="button"
              >
                就绪证据上一批
              </button>
              <button
                className={secondaryButtonClassName}
                disabled={!nextCursor}
                onClick={() => void nextPage()}
                type="button"
              >
                就绪证据下一批
              </button>
            </div>
          </section>
        </div>
      ) : (
        <EmptyPanel
          title="尚无服务就绪证据"
          description="机构完成审批后由服务端计算；如已完成，请刷新或联系平台核对。"
        />
      )}
    </main>
  );
}

function readinessError(error: unknown) {
  if (!isApiError(error)) return "证据加载失败，请重试。";
  if (error.status === 401) return "登录状态已失效，请重新登录。";
  if (error.status === 403) return "当前账号无权查看该机构的服务资格。";
  if (error.status === 404) return "服务端尚未生成就绪证据，请完成入驻审批后重试。";
  if (error.status === 503) return "服务暂时不可用，当前页面未猜测就绪结果，请稍后重试。";
  return "证据加载失败，请重试。";
}
function reasonLabel(value: ReadinessReason) {
  const labels: Record<ReadinessReason, string> = {
    COMPLIANCE_SUSPENDED: "机构当前处于合规暂停状态",
    INSTITUTION_APPROVAL_SOURCE_INVALID: "机构审批来源或版本需要平台核对",
    INSTITUTION_LICENSE_INVALID: "机构许可证缺失、尚未生效或已过期",
    METABOLIC_SCOPE_MISSING: "机构与健管师的代谢健康服务范围尚未匹配",
    NO_APPROVED_ACTIVE_THERAPIST: "暂无审核通过且资质有效的健管师",
    TENANT_NOT_ACTIVE: "机构经营主体尚未激活",
  };
  return labels[value] ?? "未识别的就绪条件，请刷新或联系平台核对";
}
function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待确认"
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}
