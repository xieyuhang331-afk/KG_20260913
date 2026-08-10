import { ArrowLeft, CheckCircle2, LockKeyhole, RefreshCw, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

type Decision = "approve" | "reject";

export function IdentityReviewDetailPage() {
  const { userId } = useParams();
  const numericUserId = Number(userId);

  if (!Number.isFinite(numericUserId) || numericUserId <= 0) {
    return <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700">无效的审核对象。</div>;
  }

  return <IdentityReviewDetailView userId={numericUserId} onRefresh={() => window.location.reload()} />;
}

export function IdentityReviewDetailView({
  userId,
  conflict = false,
  isSubmitting = false,
  onRefresh,
}: {
  userId: number;
  conflict?: boolean;
  isSubmitting?: boolean;
  onRefresh: () => void;
}) {
  const [decision, setDecision] = useState<Decision | null>(null);

  return (
    <div className="space-y-5">
      <Link className="inline-flex items-center gap-2 text-sm font-medium text-pine" to="/platform/identity-reviews">
        <ArrowLeft size={16} />
        返回待审核队列
      </Link>

      <header className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-pine">Identity Review Detail</p>
        <h1 className="mt-2 text-2xl font-semibold text-ink">实名认证审核详情</h1>
        <p className="mt-2 text-sm text-slate-500">审核对象：用户 #{userId}</p>
      </header>

      {conflict ? (
        <section className="flex flex-col gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm text-amber-800">审核状态已变化，请刷新后再决定。</p>
          <button
            className="inline-flex items-center justify-center gap-2 rounded-md border border-amber-300 bg-white px-3 py-2 text-sm font-medium text-amber-800"
            onClick={onRefresh}
            type="button"
          >
            <RefreshCw size={16} />
            刷新审核状态
          </button>
        </section>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
        <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-start gap-3">
            <span className="rounded-lg bg-mint p-2.5 text-pine">
              <LockKeyhole size={20} />
            </span>
            <div>
              <h2 className="font-semibold text-ink">敏感身份信息默认隐藏</h2>
              <p className="mt-2 text-sm leading-6 text-slate-500">
                当前页面不会请求或持久缓存完整身份信息。后端 Step-up/Re-auth 合同通过门禁后，才允许临时查看。
              </p>
            </div>
          </div>

          <div className="mt-6 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-5 py-10 text-center">
            <p className="text-sm font-medium text-ink">完整身份信息受二次认证保护</p>
            <p className="mt-2 text-xs text-slate-500">禁止进入 URL、持久缓存、日志、埋点和错误报告。</p>
            <button
              className="mt-5 rounded-md bg-slate-200 px-4 py-2 text-sm font-medium text-slate-500"
              disabled
              type="button"
            >
              等待二次认证能力
            </button>
          </div>
        </section>

        <aside className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="font-semibold text-ink">审核操作</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            操作入口已完成安全骨架；敏感合同门禁关闭前不会提交真实决策。
          </p>
          <div className="mt-5 space-y-3">
            <button
              className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-pine px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50"
              disabled={isSubmitting}
              onClick={() => setDecision("approve")}
              type="button"
            >
              <CheckCircle2 size={17} />
              审核通过
            </button>
            <button
              className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-coral px-4 py-2.5 text-sm font-medium text-coral disabled:opacity-50"
              disabled={isSubmitting}
              onClick={() => setDecision("reject")}
              type="button"
            >
              <XCircle size={17} />
              审核驳回
            </button>
          </div>
        </aside>
      </div>

      {decision ? <DecisionDialog decision={decision} onCancel={() => setDecision(null)} /> : null}
    </div>
  );
}

function DecisionDialog({ decision, onCancel }: { decision: Decision; onCancel: () => void }) {
  const approving = decision === "approve";
  const title = approving ? "确认审核通过" : "确认审核驳回";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/45 px-4" role="presentation">
      <section aria-label={title} className="w-full max-w-md rounded-xl bg-white p-6 shadow-xl" role="dialog">
        <h2 className="text-lg font-semibold text-ink">{title}</h2>
        <p className="mt-3 text-sm leading-6 text-slate-600">
          {approving
            ? "通过会改变实名认证终态。后端幂等、服务端决策时间和 Step-up 合同完成前不可提交。"
            : "驳回会改变实名认证终态。固定原因码和服务端决策时间合同完成前不可提交。"}
        </p>
        <div className="mt-6 flex justify-end gap-3">
          <button className="rounded-md border border-slate-200 px-4 py-2 text-sm" onClick={onCancel} type="button">
            取消
          </button>
          <button
            className="rounded-md bg-slate-200 px-4 py-2 text-sm font-medium text-slate-500"
            disabled
            type="button"
          >
            {approving ? "等待后端合同后提交通过" : "等待后端合同后提交驳回"}
          </button>
        </div>
      </section>
    </div>
  );
}
