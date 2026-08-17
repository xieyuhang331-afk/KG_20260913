import { useCallback, useEffect, useState } from "react";
import { isApiError } from "@/shared/api/errors";
import {
  ConfirmDialog,
  EmptyPanel,
  Feedback,
  LoadingPanel,
  primaryButtonClassName,
  secondaryButtonClassName,
} from "@/domains/institution/受控入驻界面";
import { exitTherapist, getTherapistReview, listTherapistReviews, resumeTherapist, suspendTherapist } from "../api";
import type { TherapistReviewDetail, TherapistReviewItem } from "../types";

type Action = "suspend" | "resume" | "exit";
const labels: Record<Action, [string, string]> = {
  suspend: ["暂停健管师服务", "确认暂停"],
  resume: ["恢复健管师服务", "确认恢复"],
  exit: ["确认健管师退出", "确认退出"],
};
function errorMessage(error: unknown, fallback: string) {
  if (!isApiError(error)) return fallback;
  if (error.status === 409) return "状态已变化，已刷新最新数据，请重新确认。";
  if (error.status === 503) return "服务暂不可用，结果暂时无法确认，请刷新后核对。";
  if (error.status === 404) return "健管师不存在或已安全隐藏。";
  return fallback;
}
export function TherapistStatusPage() {
  const [items, setItems] = useState<TherapistReviewItem[]>([]);
  const [detail, setDetail] = useState<TherapistReviewDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<Action | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setItems((await listTherapistReviews({ limit: 20 })).items);
    } catch (reason) {
      setItems([]);
      setError(errorMessage(reason, "状态列表加载失败，请重试。"));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  async function open(item: TherapistReviewItem) {
    setError("");
    try {
      setDetail(await getTherapistReview(item.therapist_id));
    } catch (reason) {
      setError(errorMessage(reason, "状态详情加载失败，请重试。"));
    }
  }
  async function mutate() {
    if (!detail || !pending) return;
    const action = pending;
    setBusy(true);
    setError("");
    try {
      if (action === "resume")
        await resumeTherapist(detail.profile.therapist_id, detail.profile.version, crypto.randomUUID());
      else if (action === "exit")
        await exitTherapist(detail.profile.therapist_id, detail.profile.version, "PLATFORM_EXIT", crypto.randomUUID());
      else
        await suspendTherapist(
          detail.profile.therapist_id,
          detail.profile.version,
          "COMPLIANCE_SUSPENDED",
          crypto.randomUUID(),
        );
      setPending(null);
      setNotice("状态决定已记录");
      setDetail(await getTherapistReview(detail.profile.therapist_id));
    } catch (reason) {
      setPending(null);
      if (isApiError(reason) && reason.status === 409) {
        try {
          setDetail(await getTherapistReview(detail.profile.therapist_id));
        } catch {
          setDetail(null);
        }
      }
      setError(errorMessage(reason, "状态操作失败，请刷新后重试。"));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="space-y-5 pb-10">
      <header>
        <p className="text-sm font-medium text-teal-700">平台治理 / 健管师状态</p>
        <h1 className="mt-1 text-2xl font-semibold">健管师服务状态</h1>
        <p className="mt-2 text-sm text-slate-600">按服务端版本执行暂停、恢复或退出。</p>
      </header>
      <Feedback message={error || notice} tone={error ? "error" : "success"} />
      {loading ? (
        <LoadingPanel label="正在加载健管师状态…" />
      ) : !items.length ? (
        <EmptyPanel title="暂无可治理的健管师" description="资质审核通过后可在这里治理服务状态。" />
      ) : (
        <section className="rounded-xl border bg-white">
          {items.map((item) => (
            <article className="flex items-center justify-between border-b p-4 last:border-0" key={item.review_item_id}>
              <div>
                <strong>健管师服务记录</strong>
                <p className="text-sm text-slate-500">
                  {item.review_kind} · 版本 {item.version}
                </p>
              </div>
              <button className={secondaryButtonClassName} onClick={() => void open(item)} type="button">
                查看状态详情
              </button>
            </article>
          ))}
        </section>
      )}
      {detail ? (
        <section className="rounded-xl border bg-white p-5">
          <p className="text-sm text-slate-500">健管师状态详情</p>
          <h2 className="text-xl font-semibold">{detail.profile.display_name || "未设置展示名称"}</h2>
          <p className="mt-2 text-sm">
            在服 {detail.profile.active_case_count} / 容量上限 {detail.profile.capacity_limit}
          </p>
          <span className="mt-3 inline-block rounded-full bg-blue-50 px-3 py-1 text-sm text-blue-700">
            {detail.profile.status}
          </span>
          <div className="mt-6 flex justify-end gap-2">
            {detail.profile.status === "SUSPENDED" ? (
              <button className={primaryButtonClassName} onClick={() => setPending("resume")} type="button">
                恢复服务
              </button>
            ) : detail.profile.status === "APPROVED_ACTIVE" ? (
              <button className={secondaryButtonClassName} onClick={() => setPending("suspend")} type="button">
                暂停服务
              </button>
            ) : null}
            {detail.profile.status !== "EXITED" ? (
              <button className={secondaryButtonClassName} onClick={() => setPending("exit")} type="button">
                退出
              </button>
            ) : null}
          </div>
        </section>
      ) : null}
      {pending ? (
        <ConfirmDialog
          busy={busy}
          confirmLabel={labels[pending][1]}
          description="该操作不会因冲突自动重试，请确认当前版本。"
          destructive={pending !== "resume"}
          onClose={() => setPending(null)}
          onConfirm={() => void mutate()}
          title={labels[pending][0]}
        />
      ) : null}
    </main>
  );
}
