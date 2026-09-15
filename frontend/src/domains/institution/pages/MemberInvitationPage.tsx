import { Clock3, MailPlus, RefreshCw, RotateCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createMemberInvitation, listMemberInvitations, resendMemberInvitation, revokeMemberInvitation } from "../api";
import type { MemberInvitation, MemberInvitationMode, MemberInvitationStatus } from "../types";
import { createIdempotencyKey, getSafeApiError } from "@/shared/api/slice3";
import {
  EmptyPanel,
  Feedback,
  LoadingPanel,
  fieldClassName,
  primaryButtonClassName,
  secondaryButtonClassName,
} from "../受控入驻界面";

type FeedbackState = { message: string; tone: "info" | "success" | "error" };

export function MemberInvitationPage() {
  const [items, setItems] = useState<MemberInvitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<FeedbackState>({ message: "", tone: "info" });
  const [status, setStatus] = useState<MemberInvitationStatus | "">("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | undefined>();
  const [history, setHistory] = useState<Array<string | undefined>>([]);
  const [secret, setSecret] = useState<string | null>(null);
  const [mode, setMode] = useState<MemberInvitationMode>("SELF");
  const phoneRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(
    async (targetCursor?: string, targetStatus: MemberInvitationStatus | "" = status) => {
      setLoading(true);
      try {
        const page = await listMemberInvitations({
          cursor: targetCursor,
          limit: 20,
          status: targetStatus || undefined,
        });
        setItems(page.items);
        setNextCursor(page.next_cursor ?? null);
        setFeedback((current) => (current.tone === "error" ? { message: "", tone: "info" } : current));
      } catch (error) {
        setFeedback({ message: getSafeApiError(error).message, tone: "error" });
      } finally {
        setLoading(false);
      }
    },
    [status],
  );

  useEffect(() => {
    void load();
    return () => setSecret(null);
  }, [load]);

  useEffect(() => {
    if (secret) closeRef.current?.focus();
  }, [secret]);

  function closeSecret() {
    setSecret(null);
    window.setTimeout(() => phoneRef.current?.focus(), 0);
  }

  function handleSecretKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeSecret();
    } else if (event.key === "Tab") {
      event.preventDefault();
      closeRef.current?.focus();
    }
  }

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const phone = phoneRef.current?.value.trim() ?? "";
    if (!/^1[3-9][0-9]{9}$/.test(phone)) {
      setFeedback({ message: "请输入有效的 11 位大陆手机号。", tone: "error" });
      phoneRef.current?.focus();
      return;
    }
    setBusyId("create");
    try {
      const value = await createMemberInvitation({ mode, phone }, createIdempotencyKey());
      setSecret(value.short_code);
      if (phoneRef.current) phoneRef.current.value = "";
      await load(cursor);
      setFeedback({ message: "邀请已创建。短码关闭后不会保留。", tone: "success" });
    } catch (error) {
      const safe = getSafeApiError(error);
      if (safe.refreshRequired) await load(cursor);
      setFeedback({ message: safe.message, tone: "error" });
    } finally {
      setBusyId(null);
    }
  }

  async function resend(item: MemberInvitation) {
    if (!window.confirm("确认重发？旧短码将失效，新短码只展示一次。")) return;
    setBusyId(item.invitation_id);
    try {
      const value = await resendMemberInvitation(item.invitation_id, item.version, createIdempotencyKey());
      setSecret(value.short_code);
      await load(cursor);
      setFeedback({ message: "邀请已重发。", tone: "success" });
    } catch (error) {
      const safe = getSafeApiError(error);
      if (safe.refreshRequired) await load(cursor);
      setFeedback({ message: safe.message, tone: "error" });
    } finally {
      setBusyId(null);
    }
  }

  async function revoke(item: MemberInvitation) {
    if (!window.confirm("确认撤销该邀请？撤销后不能恢复。")) return;
    setBusyId(item.invitation_id);
    try {
      await revokeMemberInvitation(item.invitation_id, item.version, "INSTITUTION_CANCELLED", createIdempotencyKey());
      await load(cursor);
      setFeedback({ message: "邀请已撤销。", tone: "success" });
    } catch (error) {
      const safe = getSafeApiError(error);
      if (safe.refreshRequired) await load(cursor);
      setFeedback({ message: safe.message, tone: "error" });
    } finally {
      setBusyId(null);
    }
  }

  async function changeStatus(value: MemberInvitationStatus | "") {
    setStatus(value);
    setCursor(undefined);
    setHistory([]);
    await load(undefined, value);
  }

  async function next() {
    if (!nextCursor) return;
    setHistory((values) => [...values, cursor]);
    setCursor(nextCursor);
    await load(nextCursor);
  }

  async function previous() {
    const value = history[history.length - 1];
    setHistory((values) => values.slice(0, -1));
    setCursor(value);
    await load(value);
  }

  return (
    <main className="mx-auto w-full max-w-[1280px] space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">客户服务 / 受控邀约</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">客户服务邀约</h1>
          <p className="mt-2 text-sm text-slate-500">
            邀请本人或代办长者建立本机构健康管理服务关系，不代表会员购买或权益开通。
          </p>
        </div>
        <button
          className={secondaryButtonClassName}
          disabled={loading || busyId !== null}
          onClick={() => void load(cursor)}
          type="button"
        >
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>

      <Feedback message={feedback.message} tone={feedback.tone} />

      <div className="grid items-start gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel xl:sticky xl:top-24">
          <div className="flex items-start gap-3">
            <span className="rounded-lg bg-teal-50 p-2 text-teal-700">
              <MailPlus aria-hidden="true" size={20} />
            </span>
            <div>
              <h2 className="font-semibold text-slate-950">创建客户服务邀约</h2>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                短码只会展示一次，不写入浏览器存储、地址栏或日志。
              </p>
            </div>
          </div>
          <form className="mt-5 grid gap-3" onSubmit={create}>
            <label className="text-sm font-medium text-slate-700">
              邀请模式
              <select
                className={fieldClassName}
                onChange={(event) => setMode(event.target.value as MemberInvitationMode)}
                value={mode}
              >
                <option value="SELF">本人接入服务</option>
                <option value="PROXY_ELDER">代办长者接入服务</option>
              </select>
            </label>
            <label className="text-sm font-medium text-slate-700">
              接收手机号
              <input
                aria-label="接收手机号"
                autoComplete="off"
                className={fieldClassName}
                inputMode="numeric"
                maxLength={11}
                ref={phoneRef}
                required
              />
            </label>
            <button className={primaryButtonClassName} disabled={busyId !== null} type="submit">
              {busyId === "create" ? "创建中…" : "创建邀请"}
            </button>
          </form>
        </section>

        <section className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
            <div>
              <h2 className="font-semibold text-slate-950">邀请记录</h2>
              <p className="mt-1 text-xs text-slate-500">使用服务端 cursor 浏览批次，不推算总页数。</p>
            </div>
            <select
              aria-label="邀请状态"
              className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
              onChange={(event) => void changeStatus(event.target.value as MemberInvitationStatus | "")}
              value={status}
            >
              <option value="">全部状态</option>
              <option value="INVITED">待接受</option>
              <option value="ACCEPTED">已接受</option>
              <option value="REVOKED">已撤销</option>
              <option value="EXPIRED">已到期</option>
            </select>
          </div>
          {loading ? (
            <div className="p-5">
              <LoadingPanel label="正在加载客户服务邀约…" />
            </div>
          ) : items.length === 0 ? (
            <div className="p-5">
              <EmptyPanel title="当前批次没有邀请" description="创建邀请，或调整状态筛选后重试。" />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px] text-left text-sm">
                <thead className="bg-slate-50 text-xs text-slate-500">
                  <tr>
                    <th className="px-5 py-3">接收人</th>
                    <th className="px-5 py-3">模式</th>
                    <th className="px-5 py-3">状态</th>
                    <th className="px-5 py-3">有效期</th>
                    <th className="px-5 py-3 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {items.map((item) => (
                    <tr key={item.invitation_id}>
                      <td className="px-5 py-4 font-medium">{item.phone_masked}</td>
                      <td className="px-5 py-4">{item.mode === "SELF" ? "本人" : "代办老人"}</td>
                      <td className="px-5 py-4">
                        <Status status={item.status} />
                      </td>
                      <td className="px-5 py-4 text-slate-500">
                        <Clock3 aria-hidden="true" className="mr-1 inline" size={14} />
                        {formatTime(item.expires_at)}
                      </td>
                      <td className="space-x-2 px-5 py-4 text-right">
                        {item.status === "INVITED" ? (
                          <>
                            <button
                              className={secondaryButtonClassName}
                              disabled={busyId !== null}
                              onClick={() => void resend(item)}
                              type="button"
                            >
                              <RotateCw aria-hidden="true" className="mr-1 inline" size={14} />
                              重发
                            </button>
                            <button
                              className={secondaryButtonClassName}
                              disabled={busyId !== null}
                              onClick={() => void revoke(item)}
                              type="button"
                            >
                              撤销
                            </button>
                          </>
                        ) : (
                          <span className="text-xs text-slate-400">无可用操作</span>
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
      </div>

      {secret ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 p-4">
          <section
            aria-labelledby="member-secret-title"
            aria-modal="true"
            className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"
            onKeyDown={handleSecretKeyDown}
            role="dialog"
          >
            <ShieldCheck aria-hidden="true" className="text-teal-700" size={28} />
            <h2 className="mt-3 text-lg font-semibold" id="member-secret-title">
              一次性邀请短码
            </h2>
            <p className="mt-2 text-sm text-slate-600">仅本次展示。请通过受控渠道交付，关闭后页面立即清除。</p>
            <p className="my-5 rounded-xl bg-slate-950 px-5 py-4 text-center font-mono text-2xl tracking-[0.35em] text-white">
              {secret}
            </p>
            <button className={`${primaryButtonClassName} w-full`} onClick={closeSecret} ref={closeRef} type="button">
              我已安全记录
            </button>
          </section>
        </div>
      ) : null}
    </main>
  );
}

function Status({ status }: { status: MemberInvitationStatus }) {
  const label = { INVITED: "待接受", ACCEPTED: "已接受", REVOKED: "已撤销", EXPIRED: "已到期" }[status];
  const tone =
    status === "ACCEPTED"
      ? "bg-emerald-50 text-emerald-700"
      : status === "INVITED"
        ? "bg-blue-50 text-blue-700"
        : "bg-slate-100 text-slate-600";
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>{label}</span>;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待确认"
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(date);
}
