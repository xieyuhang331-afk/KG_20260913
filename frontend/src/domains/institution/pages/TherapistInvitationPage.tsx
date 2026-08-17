import { Clock3, MailPlus, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { isApiError } from "@/shared/api/errors";
import {
  ConfirmDialog,
  EmptyPanel,
  Feedback,
  LoadingPanel,
  fieldClassName,
  primaryButtonClassName,
  secondaryButtonClassName,
} from "../受控入驻界面";
import { createTherapistInvitation, listTherapistInvitations, revokeTherapistInvitation } from "../api";
import type { TherapistInvitation } from "../types";

type FeedbackState = { message: string; tone: "info" | "success" | "error" };

export function TherapistInvitationPage() {
  const [items, setItems] = useState<TherapistInvitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<FeedbackState>({ message: "", tone: "info" });
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | undefined>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([]);
  const [pendingRevoke, setPendingRevoke] = useState<TherapistInvitation | null>(null);
  const [shortCode, setShortCode] = useState<string | null>(null);
  const phoneRef = useRef<HTMLInputElement>(null);
  const shortCodeCloseRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (shortCode) shortCodeCloseRef.current?.focus();
  }, [shortCode]);

  const load = useCallback(async (targetCursor?: string) => {
    setLoading(true);
    try {
      const value = await listTherapistInvitations(targetCursor ? { cursor: targetCursor, limit: 20 } : { limit: 20 });
      setItems(value.items);
      setNextCursor(value.next_cursor ?? null);
      setFeedback((current) => (current.tone === "error" ? { message: "", tone: "info" } : current));
    } catch (error) {
      setFeedback({ message: therapistErrorMessage(error, "邀请列表加载失败，请重试。"), tone: "error" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => setShortCode(null);
  }, [load]);

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const phone = phoneRef.current?.value.trim() ?? "";
    if (!/^1[3-9][0-9]{9}$/.test(phone)) {
      setFeedback({ message: "请输入有效的11位手机号。", tone: "error" });
      phoneRef.current?.focus();
      return;
    }
    setBusy(true);
    setFeedback({ message: "", tone: "info" });
    try {
      const value = await createTherapistInvitation({ phone, expires_in_minutes: 60 }, crypto.randomUUID());
      setShortCode(value.short_code ?? null);
      if (phoneRef.current) phoneRef.current.value = "";
      await load(cursor);
      setFeedback({ message: "邀请已创建，请通过受控渠道交付一次性短码。", tone: "success" });
    } catch (error) {
      setFeedback({ message: therapistErrorMessage(error, "邀请创建失败，请核对后重试。"), tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function confirmRevoke() {
    if (!pendingRevoke) return;
    const target = pendingRevoke;
    setBusy(true);
    try {
      await revokeTherapistInvitation(target.invitation_id, target.version, crypto.randomUUID());
      setPendingRevoke(null);
      await load(cursor);
      setFeedback({ message: "邀请已撤销。", tone: "success" });
    } catch (error) {
      setPendingRevoke(null);
      if (isApiError(error) && error.status === 409) {
        await load(cursor);
        setFeedback({ message: "邀请状态已变化，列表已刷新，请按最新状态继续。", tone: "error" });
      } else {
        setFeedback({ message: therapistErrorMessage(error, "撤销失败，请稍后重试。"), tone: "error" });
      }
    } finally {
      setBusy(false);
    }
  }

  async function nextPage() {
    if (!nextCursor) return;
    setCursorHistory((history) => [...history, cursor]);
    setCursor(nextCursor);
    await load(nextCursor);
  }

  async function previousPage() {
    const previous = cursorHistory[cursorHistory.length - 1];
    setCursorHistory((history) => history.slice(0, -1));
    setCursor(previous);
    await load(previous);
  }

  return (
    <main className="mx-auto max-w-6xl space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">人员管理 / 受控邀请</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">健管师邀请</h1>
          <p className="mt-2 text-sm text-slate-500">创建当前机构邀请，跟踪激活、到期和撤销状态。</p>
        </div>
        <button
          className={secondaryButtonClassName}
          disabled={loading || busy}
          onClick={() => void load(cursor)}
          type="button"
        >
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>

      <Feedback message={feedback.message} tone={feedback.tone} />

      <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <div className="flex items-start gap-3">
          <span className="rounded-lg bg-teal-50 p-2 text-teal-700">
            <MailPlus aria-hidden="true" size={20} />
          </span>
          <div>
            <h2 className="font-semibold text-slate-950">创建受控邀请</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">短码只展示一次，不会写入浏览器存储或地址栏。</p>
          </div>
        </div>
        <form className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={create}>
          <label className="min-w-0 flex-1 text-sm font-medium text-slate-700">
            手机号
            <input
              aria-label="手机号"
              autoComplete="off"
              className={fieldClassName}
              inputMode="numeric"
              maxLength={11}
              name="phone"
              pattern="1[3-9][0-9]{9}"
              ref={phoneRef}
              required
            />
          </label>
          <button className={primaryButtonClassName} disabled={busy} type="submit">
            {busy ? "创建中…" : "创建邀请"}
          </button>
        </form>
      </section>

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="font-semibold text-slate-950">邀请记录</h2>
            <p className="mt-1 text-xs text-slate-500">按服务端批次向前或返回，不显示虚构总页数。</p>
          </div>
          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
            当前批次 {items.length} 条
          </span>
        </div>
        {loading ? (
          <div className="p-5">
            <LoadingPanel label="正在加载邀请记录…" />
          </div>
        ) : items.length === 0 ? (
          <div className="p-5">
            <EmptyPanel title="还没有邀请记录" description="填写手机号创建第一条受控邀请；邀请过期后需重新创建。" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-[720px] w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs font-semibold text-slate-500">
                <tr>
                  <th className="px-5 py-3">手机号</th>
                  <th className="px-5 py-3">状态</th>
                  <th className="px-5 py-3">有效期</th>
                  <th className="px-5 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((item) => (
                  <tr key={item.invitation_id}>
                    <td className="px-5 py-4 font-medium text-slate-950">{item.masked_phone}</td>
                    <td className="px-5 py-4">
                      <StatusBadge status={item.status} />
                    </td>
                    <td className="px-5 py-4 text-slate-600">
                      <Clock3 aria-hidden="true" className="mr-1 inline" size={14} />
                      {formatTime(item.expires_at)}
                    </td>
                    <td className="px-5 py-4 text-right">
                      {item.status === "INVITED" ? (
                        <button
                          className={secondaryButtonClassName}
                          disabled={busy}
                          onClick={() => setPendingRevoke(item)}
                          type="button"
                        >
                          撤销邀请
                        </button>
                      ) : (
                        <span className="text-xs text-slate-400">不可操作</span>
                      )}
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
            disabled={loading || cursorHistory.length === 0}
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

      {pendingRevoke ? (
        <ConfirmDialog
          busy={busy}
          confirmLabel="确认撤销"
          description="撤销后该短码立即失效，且不能恢复；如仍需邀请必须创建新记录。"
          destructive
          onClose={() => setPendingRevoke(null)}
          onConfirm={() => void confirmRevoke()}
          title="确认撤销该邀请？"
        />
      ) : null}

      {shortCode ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
          <section
            aria-labelledby="short-code-title"
            aria-modal="true"
            className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"
            role="dialog"
          >
            <ShieldCheck aria-hidden="true" className="text-teal-700" size={28} />
            <h2 className="mt-3 text-lg font-semibold text-slate-950" id="short-code-title">
              邀请已创建
            </h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              请通过受控渠道交付以下一次性短码。关闭后页面不再保留。
            </p>
            <p className="my-5 rounded-xl bg-slate-950 px-5 py-4 text-center font-mono text-2xl tracking-[0.35em] text-white">
              {shortCode}
            </p>
            <button
              className={`${primaryButtonClassName} w-full`}
              onClick={() => setShortCode(null)}
              ref={shortCodeCloseRef}
              type="button"
            >
              我已安全记录
            </button>
          </section>
        </div>
      ) : null}
    </main>
  );
}

function StatusBadge({ status }: { status: TherapistInvitation["status"] }) {
  const values = {
    INVITED: ["待激活", "bg-blue-50 text-blue-700 ring-blue-200"],
    ACTIVATED: ["已接受", "bg-emerald-50 text-emerald-700 ring-emerald-200"],
    EXPIRED: ["已到期", "bg-slate-100 text-slate-600 ring-slate-200"],
    REVOKED: ["已撤销", "bg-red-50 text-red-700 ring-red-200"],
  } as const;
  return (
    <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${values[status][1]}`}>
      {values[status][0]}
    </span>
  );
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待确认"
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function therapistErrorMessage(error: unknown, fallback: string) {
  if (!isApiError(error)) return fallback;
  if (error.status === 401) return "登录状态已失效，请重新登录。";
  if (error.status === 403) return "当前账号无权操作该机构的健管师。";
  if (error.status === 404) return "记录不存在或已不在当前权限范围内。";
  if (error.status === 409) return "记录状态或版本已变化，请刷新后重新确认。";
  if (error.status === 429) return "请求过于频繁，请稍后再试。";
  if (error.status === 503) return "服务暂时不可用或结果尚无法确认；系统未自动重试。";
  return fallback;
}
