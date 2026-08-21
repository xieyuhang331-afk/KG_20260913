import { FileCheck2, RefreshCw } from "lucide-react";
import { useState } from "react";
import { createConsentDocument, publishConsentDocument, retireConsentDocument } from "../实名认证审核接口";
import type { ConsentDocument, ConsentDocumentType } from "../实名认证审核类型";
import { createIdempotencyKey, getSafeApiError } from "@/shared/api/slice3";

const documentLabels: Record<ConsentDocumentType, string> = {
  USER_AGREEMENT: "用户协议",
  PRIVACY_POLICY: "隐私政策",
  HEALTH_DATA_PROCESSING: "健康数据处理同意",
  INSTITUTION_SERVICE: "机构服务协议",
  NON_MEDICAL_RISK: "非医疗服务风险说明",
  PROXY_AUTHORIZATION: "代理授权书",
};

export function ConsentDocumentPage() {
  const [document, setDocument] = useState<ConsentDocument | null>(null);
  const [documentType, setDocumentType] = useState<ConsentDocumentType>("USER_AGREEMENT");
  const [semanticVersion, setSemanticVersion] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [effectiveAt, setEffectiveAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ message: string; tone: "success" | "error" } | null>(null);

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setFeedback(null);
    try {
      const value = await createConsentDocument(
        {
          document_type: documentType,
          semantic_version: semanticVersion,
          requires_reconsent: true,
          renditions: [{ locale: "zh-CN", title, body }],
        },
        createIdempotencyKey(),
      );
      setDocument(value);
      setFeedback({ message: "同意文档草稿已创建。", tone: "success" });
    } catch (error) {
      setFeedback({ message: getSafeApiError(error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (!document || !effectiveAt || !window.confirm("确认发布该版本？发布后将成为正式展示版本。")) return;
    setBusy(true);
    setFeedback(null);
    try {
      const value = await publishConsentDocument(
        document.document_version_id,
        document.version,
        new Date(effectiveAt).toISOString(),
        createIdempotencyKey(),
      );
      setDocument(value);
      setFeedback({ message: "同意文档已发布；requires_reconsent=true。", tone: "success" });
    } catch (error) {
      const safe = getSafeApiError(error);
      if (safe.refreshRequired) setDocument(null);
      setFeedback({
        message: safe.refreshRequired
          ? `${safe.message} 当前合同没有文档 GET 列表，页面已清除陈旧副本。`
          : safe.message,
        tone: "error",
      });
    } finally {
      setBusy(false);
    }
  }

  async function retire() {
    if (!document || !window.confirm("确认退役该文档版本？")) return;
    setBusy(true);
    setFeedback(null);
    try {
      const value = await retireConsentDocument(
        document.document_version_id,
        document.version,
        "SUPERSEDED_BY_NEW_VERSION",
        createIdempotencyKey(),
      );
      setDocument(value);
      setFeedback({ message: "同意文档已退役。", tone: "success" });
    } catch (error) {
      const safe = getSafeApiError(error);
      if (safe.refreshRequired) setDocument(null);
      setFeedback({
        message: safe.refreshRequired
          ? `${safe.message} 当前合同没有文档 GET 列表，页面已清除陈旧副本。`
          : safe.message,
        tone: "error",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-6xl space-y-5">
      <header>
        <p className="text-xs font-semibold tracking-wide text-teal-700">平台治理 / 版本化同意</p>
        <h1 className="mt-1 text-2xl font-semibold">同意文档</h1>
        <p className="mt-2 text-sm text-slate-500">创建包含 zh-CN rendition 的版本，发布时固定要求用户重新同意。</p>
      </header>
      {feedback ? (
        <section
          className={`rounded-xl border p-4 text-sm ${feedback.tone === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-800"}`}
          role={feedback.tone === "error" ? "alert" : "status"}
        >
          {feedback.message}
        </section>
      ) : null}
      <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
        <form className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-panel" onSubmit={create}>
          <div className="flex items-center gap-2">
            <FileCheck2 aria-hidden="true" className="text-teal-700" size={21} />
            <h2 className="font-semibold">创建文档草稿</h2>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm font-medium">
              文档类型
              <select
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
                onChange={(event) => setDocumentType(event.target.value as ConsentDocumentType)}
                value={documentType}
              >
                {Object.entries(documentLabels).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm font-medium">
              语义版本
              <input
                className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
                onChange={(event) => setSemanticVersion(event.target.value)}
                pattern="[A-Za-z0-9._-]+"
                placeholder="例如 1.0.0"
                required
                value={semanticVersion}
              />
            </label>
          </div>
          <label className="block text-sm font-medium">
            zh-CN 标题
            <input
              className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
              maxLength={160}
              onChange={(event) => setTitle(event.target.value)}
              required
              value={title}
            />
          </label>
          <label className="block text-sm font-medium">
            zh-CN 正文
            <textarea
              className="mt-1 min-h-64 w-full rounded-lg border border-slate-200 px-3 py-2 leading-6"
              maxLength={100000}
              onChange={(event) => setBody(event.target.value)}
              required
              value={body}
            />
          </label>
          <label className="flex items-start gap-2 rounded-lg bg-teal-50 p-3 text-sm text-teal-900">
            <input checked readOnly type="checkbox" />
            <span>
              <strong>requires_reconsent=true</strong>
              <br />
              发布新版本后，适用会员必须按服务端规则重新同意。
            </span>
          </label>
          <button
            className="rounded-lg bg-teal-700 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
            disabled={busy || document !== null}
            type="submit"
          >
            {busy ? "处理中…" : "创建草稿"}
          </button>
        </form>
        <aside className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h2 className="font-semibold">本次文档状态</h2>
          {document ? (
            <div className="mt-4 space-y-4">
              <dl className="space-y-3 text-sm">
                <Field label="类型" value={documentLabels[document.document_type]} />
                <Field label="语义版本" value={document.semantic_version} />
                <Field label="状态" value={document.status} />
                <Field label="版本号" value={String(document.version)} />
                <Field label="Rendition" value={document.renditions.map((item) => item.locale).join("、")} />
              </dl>
              {document.status === "DRAFT" ? (
                <>
                  <label className="block text-sm font-medium">
                    生效时间（不得晚于当前时间）
                    <input
                      className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
                      max={localDateTime(new Date())}
                      onChange={(event) => setEffectiveAt(event.target.value)}
                      type="datetime-local"
                      value={effectiveAt}
                    />
                  </label>
                  <button
                    className="w-full rounded-lg bg-teal-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                    disabled={busy || !effectiveAt}
                    onClick={() => void publish()}
                    type="button"
                  >
                    发布版本
                  </button>
                </>
              ) : null}
              {document.status === "PUBLISHED" ? (
                <button
                  className="w-full rounded-lg border border-red-200 px-4 py-2 text-sm font-semibold text-red-700 disabled:opacity-50"
                  disabled={busy}
                  onClick={() => void retire()}
                  type="button"
                >
                  退役版本
                </button>
              ) : null}
            </div>
          ) : (
            <div className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-500">
              <RefreshCw aria-hidden="true" className="mb-2" size={20} />
              <p>后端尚未提供同意文档列表 API。本页只管理本次创建响应，不伪造历史数据。</p>
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="mt-1 break-all font-medium">{value}</dd>
    </div>
  );
}
function localDateTime(date: Date) {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}
