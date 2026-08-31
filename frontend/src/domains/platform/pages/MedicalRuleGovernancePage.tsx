import { ArrowLeft, BookOpenCheck, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  createAssessmentRuleSet,
  getAssessmentRuleSet,
  getSafeSlice5Error,
  governAssessmentRuleSet,
  listAssessmentRuleSets,
  medicalRuleDigest,
  publishAssessmentRuleSet,
  reviewAssessmentRuleSet,
  submitAssessmentRuleSet,
  updateAssessmentRuleSetDraft,
  type RuleGovernanceOperation,
  type RuleGovernanceReasonCode,
  type RuleReviewReasonCode,
  type RuleSetStatus,
  type RuleSetVersionDTO,
  type RuleSetVersionDetailDTO,
} from "@/shared/api/slice5";
import { createIdempotencyKey, isUuidV7 } from "@/shared/api/slice3";
import { useAuthStore } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { EmptyPanel, Feedback, LoadingPanel, secondaryButtonClassName } from "@/domains/institution/受控入驻界面";

interface PendingMutation {
  run: () => Promise<RuleSetVersionDetailDTO>;
  successMessage: string;
}

export function MedicalRuleGovernancePage() {
  const { versionId = "" } = useParams();
  const { currentUser } = useAuthStore();
  const [items, setItems] = useState<RuleSetVersionDTO[]>([]);
  const [detail, setDetail] = useState<RuleSetVersionDetailDTO | null>(null);
  const [cursor, setCursor] = useState<string>();
  const [cursorHistory, setCursorHistory] = useState<Array<string | undefined>>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [tone, setTone] = useState<"success" | "error">("error");
  const [pendingMutation, setPendingMutation] = useState<PendingMutation | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (versionId) {
        if (!isUuidV7(versionId)) throw { status: 422 };
        setDetail(await getAssessmentRuleSet(versionId));
      } else {
        const page = await listAssessmentRuleSets({ cursor, limit: 20 });
        setItems(page.items);
        setNextCursor(page.next_cursor);
        setDetail(null);
      }
      setFeedback("");
    } catch (error) {
      setDetail(null);
      setFeedback(getSafeSlice5Error(error).message);
      setTone("error");
    } finally {
      setLoading(false);
    }
  }, [cursor, versionId]);

  useEffect(() => void load(), [load]);

  async function runMutation(request: PendingMutation) {
    if (submitting) return;
    setSubmitting(true);
    try {
      const result = await request.run();
      setDetail(result);
      setPendingMutation(null);
      setTone("success");
      setFeedback(request.successMessage);
    } catch (error) {
      const safe = getSafeSlice5Error(error);
      if (safe.resultUnknown) setPendingMutation(request);
      if (safe.refreshRequired) await load();
      setTone("error");
      setFeedback(safe.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <LoadingPanel label="正在加载医学规则治理…" />;

  const expert = currentUser?.role === USER_ROLES.expert;
  const governance = currentUser?.role === USER_ROLES.sysAdmin || currentUser?.role === USER_ROLES.superAdmin;

  return (
    <main className="space-y-5">
      {versionId ? (
        <Link
          className="inline-flex items-center gap-2 text-sm font-semibold text-primary-600"
          to="/platform/assessment-rule-sets"
        >
          <ArrowLeft aria-hidden="true" size={16} /> 返回规则版本列表
        </Link>
      ) : null}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">医学内容治理 / 确定性评估</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">医学规则治理</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
            规则内容由后端闭合合同约束；页面只治理正式版本、审核与生命周期，不提供自由规则编辑。
          </p>
        </div>
        <button className={secondaryButtonClassName} onClick={() => void load()} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} /> 刷新
        </button>
      </header>
      <Feedback message={feedback} tone={tone} />
      {pendingMutation ? (
        <button
          className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2 text-sm font-semibold text-amber-900 disabled:opacity-50"
          disabled={submitting}
          onClick={() => void runMutation(pendingMutation)}
          type="button"
        >
          查询并确认原请求结果
        </button>
      ) : null}
      {detail ? (
        <RuleDetail
          detail={detail}
          disabled={submitting}
          expert={expert}
          governance={governance}
          onMutate={runMutation}
        />
      ) : versionId ? (
        <EmptyPanel title="规则版本暂不可用" description="请核对当前权限或刷新后重试。" />
      ) : (
        <RuleList
          canGoBack={cursorHistory.length > 0}
          canGoNext={Boolean(nextCursor)}
          expert={expert}
          items={items}
          onCreate={(request) => void runMutation(request)}
          onNext={() => {
            if (!nextCursor) return;
            setCursorHistory((current) => [...current, cursor]);
            setCursor(nextCursor);
          }}
          onPrevious={() => {
            setCursorHistory((current) => {
              const copy = [...current];
              setCursor(copy.pop());
              return copy;
            });
          }}
        />
      )}
    </main>
  );
}

function RuleList({
  items,
  expert,
  canGoBack,
  canGoNext,
  onPrevious,
  onNext,
  onCreate,
}: {
  items: RuleSetVersionDTO[];
  expert: boolean;
  canGoBack: boolean;
  canGoNext: boolean;
  onPrevious: () => void;
  onNext: () => void;
  onCreate: (request: PendingMutation) => void;
}) {
  return (
    <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-4">
        <article className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
          <div className="grid grid-cols-[minmax(0,1fr)_110px_140px_140px] gap-3 border-b border-slate-100 bg-slate-50 px-5 py-3 text-xs font-semibold text-slate-500 max-md:hidden">
            <span>规则版本</span>
            <span>版本</span>
            <span>状态</span>
            <span>医学负责人</span>
          </div>
          {items.length ? (
            <div className="divide-y divide-slate-100">
              {items.map((item) => (
                <Link
                  className="grid gap-3 px-5 py-4 md:grid-cols-[minmax(0,1fr)_110px_140px_140px] md:items-center"
                  key={item.rule_set_version_id}
                  to={`/platform/assessment-rule-sets/${item.rule_set_version_id}`}
                >
                  <div>
                    <p className="font-semibold text-slate-950">成人基础医学规则</p>
                    <p className="mt-1 text-xs text-slate-500">覆盖 {item.module_metadata.length} 个已批准模块</p>
                  </div>
                  <span className="text-sm">V{item.version_no}</span>
                  <RuleStatus status={item.status} />
                  <span className="truncate text-sm text-slate-700">{item.author_ref.display_name}</span>
                </Link>
              ))}
            </div>
          ) : (
            <EmptyPanel
              title="暂无医学规则版本"
              description="首个闭合规则载荷需由正式后端基线导入，前端不会硬编码医学规则。"
            />
          )}
        </article>
        <div className="flex justify-end gap-2">
          <button className={secondaryButtonClassName} disabled={!canGoBack} onClick={onPrevious} type="button">
            上一批
          </button>
          <button className={secondaryButtonClassName} disabled={!canGoNext} onClick={onNext} type="button">
            下一批
          </button>
        </div>
      </div>
      {expert ? <CreateDraftPanel items={items} onCreate={onCreate} /> : <GovernanceBoundary />}
    </section>
  );
}

function CreateDraftPanel({
  items,
  onCreate,
}: {
  items: RuleSetVersionDTO[];
  onCreate: (request: PendingMutation) => void;
}) {
  const [sourceId, setSourceId] = useState(items[0]?.rule_set_version_id ?? "");
  const [versionNo, setVersionNo] = useState(String(Math.max(1, ...items.map((item) => item.version_no + 1))));
  const [evidenceRef, setEvidenceRef] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextVersion = Number(versionNo);
    if (!sourceId || !Number.isInteger(nextVersion) || nextVersion < 1 || !evidenceRef.trim()) {
      setError("请选择服务端现有规则基线，并填写有效版本号和审批依据引用。");
      return;
    }
    if (!window.confirm("确认基于该服务端规则版本创建新草稿？")) return;
    try {
      const source = await getAssessmentRuleSet(sourceId);
      const digest = await medicalRuleDigest(source.typed_rule_payload);
      const key = createIdempotencyKey();
      const input = {
        rule_set_code: "CN_ADULT_BASELINE_V1" as const,
        version_no: nextVersion,
        typed_rule_payload: source.typed_rule_payload,
        medical_content_digest: digest,
        approval_evidence_ref: evidenceRef.trim(),
      };
      setError("");
      onCreate({ run: () => createAssessmentRuleSet(input, key), successMessage: "规则草稿已创建。" });
    } catch (cause) {
      setError(getSafeSlice5Error(cause).message);
    }
  }

  return (
    <aside className="rounded-xl border border-primary-100 bg-white p-5 shadow-panel">
      <div className="flex items-center gap-2">
        <BookOpenCheck aria-hidden="true" className="text-primary-600" size={20} />
        <h2 className="font-semibold">创建规则草稿</h2>
      </div>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        仅复制服务端闭合规则载荷，不在浏览器中编辑阈值或启用延期规则。
      </p>
      <Feedback message={error} tone="error" />
      <form className="mt-4 space-y-3" onSubmit={(event) => void submit(event)}>
        <label className="block text-sm font-medium text-slate-700">
          规则基线
          <select
            className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 px-3"
            value={sourceId}
            onChange={(event) => setSourceId(event.target.value)}
          >
            <option value="">请选择</option>
            {items.map((item) => (
              <option key={item.rule_set_version_id} value={item.rule_set_version_id}>
                版本 {item.version_no} · {ruleStatusLabel(item.status)}
              </option>
            ))}
          </select>
        </label>
        <TextInput label="新版本号" onChange={setVersionNo} type="number" value={versionNo} />
        <TextInput label="审批依据引用" onChange={setEvidenceRef} value={evidenceRef} />
        <button
          className="min-h-10 w-full rounded-lg bg-primary-600 px-4 text-sm font-semibold text-white disabled:opacity-50"
          disabled={!items.length}
          type="submit"
        >
          创建草稿
        </button>
      </form>
    </aside>
  );
}

function RuleDetail({
  detail,
  expert,
  governance,
  disabled,
  onMutate,
}: {
  detail: RuleSetVersionDetailDTO;
  expert: boolean;
  governance: boolean;
  disabled: boolean;
  onMutate: (request: PendingMutation) => Promise<void>;
}) {
  const [evidenceRef, setEvidenceRef] = useState(detail.approval_evidence_ref ?? "");
  const [reviewReason, setReviewReason] = useState<RuleReviewReasonCode>("RULE_CONTENT_CORRECTION_REQUIRED");
  const [governanceReason, setGovernanceReason] = useState<RuleGovernanceReasonCode>("MEDICAL_SAFETY_REVIEW_REQUIRED");

  async function saveDraft() {
    if (!evidenceRef.trim() || !window.confirm("确认保存当前草稿的审批依据？")) return;
    const digest = await medicalRuleDigest(detail.typed_rule_payload);
    const key = createIdempotencyKey();
    const input = {
      expected_version: detail.version,
      typed_rule_payload: detail.typed_rule_payload,
      medical_content_digest: digest,
      approval_evidence_ref: evidenceRef.trim(),
    };
    await onMutate({
      run: () => updateAssessmentRuleSetDraft(detail.rule_set_version_id, input, key),
      successMessage: "规则草稿已保存。",
    });
  }

  function versionMutation(label: string, run: (key: string) => Promise<RuleSetVersionDetailDTO>) {
    if (!window.confirm(`确认${label}？`)) return;
    const key = createIdempotencyKey();
    void onMutate({ run: () => run(key), successMessage: `${label}已提交。` });
  }

  function review(decision: "APPROVE" | "NEEDS_CORRECTION") {
    const reasonCode = decision === "APPROVE" ? "MEDICAL_CONTENT_APPROVED" : reviewReason;
    versionMutation(decision === "APPROVE" ? "批准医学规则" : "要求规则补正", (key) =>
      reviewAssessmentRuleSet(
        detail.rule_set_version_id,
        { expected_version: detail.version, decision, reason_code: reasonCode },
        key,
      ),
    );
  }

  function govern(operation: RuleGovernanceOperation) {
    const reason = reasonForOperation(operation, governanceReason);
    versionMutation(ruleOperationLabel(operation), (key) =>
      governAssessmentRuleSet(
        detail.rule_set_version_id,
        operation,
        { expected_version: detail.version, operation, reason_code: reason },
        key,
      ),
    );
  }

  const editableDraft = expert && (detail.status === "DRAFT" || detail.status === "NEEDS_CORRECTION");
  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-5">
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold text-slate-500">成人基础医学规则</p>
              <h2 className="mt-1 text-xl font-semibold">版本 {detail.version_no}</h2>
            </div>
            <RuleStatus status={detail.status} />
          </div>
          <dl className="mt-5 grid gap-4 border-t border-slate-100 pt-5 sm:grid-cols-2">
            <Summary label="作者" value={detail.author_ref.display_name} />
            <Summary label="审核人" value={detail.reviewer_ref?.display_name ?? "尚未审核"} />
            <Summary label="审核状态" value={approvalLabel(detail.approval_state)} />
            <Summary label="生效时间" value={formatTime(detail.effective_from)} />
          </dl>
        </article>
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" className="text-teal-700" size={19} />
            <h2 className="font-semibold">闭合规则模块（只读）</h2>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {detail.typed_rule_payload.modules.map((module) => (
              <div className="rounded-lg bg-slate-50 p-4" key={module.module_code}>
                <p className="font-semibold">{moduleLabel(module.module_code)}</p>
                <p className="mt-2 text-sm text-slate-600">已批准规则 {module.included_rules.length} 项</p>
                <p className="mt-1 text-sm text-slate-600">延期规则 {module.deferred_rules.length} 项（保持停用）</p>
              </div>
            ))}
          </div>
        </article>
      </div>
      <aside className="space-y-5">
        <article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
          <h2 className="font-semibold">治理操作</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            角色与状态转换均由服务端再次校验；冲突后只刷新权威状态。
          </p>
          <div className="mt-4 space-y-3">
            {editableDraft ? (
              <>
                <TextInput label="审批依据引用" onChange={setEvidenceRef} value={evidenceRef} />
                <button className={primaryButton} disabled={disabled} onClick={() => void saveDraft()} type="button">
                  保存草稿
                </button>
                <button
                  className={secondaryButtonClassName}
                  disabled={disabled}
                  onClick={() =>
                    versionMutation("提交医学审核", (key) =>
                      submitAssessmentRuleSet(detail.rule_set_version_id, detail.version, key),
                    )
                  }
                  type="button"
                >
                  提交医学审核
                </button>
              </>
            ) : null}
            {expert && detail.status === "IN_REVIEW" && detail.approval_state === "PENDING" ? (
              <>
                <label className="block text-sm font-medium text-slate-700">
                  补正原因
                  <select
                    className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 px-3"
                    value={reviewReason}
                    onChange={(event) => setReviewReason(event.target.value as RuleReviewReasonCode)}
                  >
                    {correctionReasons.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <button className={primaryButton} disabled={disabled} onClick={() => review("APPROVE")} type="button">
                  批准
                </button>
                <button
                  className={secondaryButtonClassName}
                  disabled={disabled}
                  onClick={() => review("NEEDS_CORRECTION")}
                  type="button"
                >
                  要求补正
                </button>
              </>
            ) : null}
            {governance && detail.status === "IN_REVIEW" && detail.approval_state === "APPROVED" ? (
              <button
                className={primaryButton}
                disabled={disabled}
                onClick={() =>
                  versionMutation("发布规则版本", (key) =>
                    publishAssessmentRuleSet(
                      detail.rule_set_version_id,
                      {
                        expected_version: detail.version,
                        operation: "PUBLISH",
                        reason_code: "DOUBLE_SIGNED_BASELINE_RELEASE",
                        effective_from: new Date().toISOString(),
                      },
                      key,
                    ),
                  )
                }
                type="button"
              >
                发布规则版本
              </button>
            ) : null}
            {governance && (detail.status === "PUBLISHED" || detail.status === "SUSPENDED") ? (
              <>
                <label className="block text-sm font-medium text-slate-700">
                  治理原因
                  <select
                    className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 px-3"
                    value={governanceReason}
                    onChange={(event) => setGovernanceReason(event.target.value as RuleGovernanceReasonCode)}
                  >
                    {governanceReasons.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                {detail.status === "PUBLISHED" ? (
                  <button
                    className={secondaryButtonClassName}
                    disabled={disabled}
                    onClick={() => govern("SUSPEND")}
                    type="button"
                  >
                    暂停规则版本
                  </button>
                ) : (
                  <button className={primaryButton} disabled={disabled} onClick={() => govern("RESUME")} type="button">
                    恢复规则版本
                  </button>
                )}
                <button
                  className="min-h-10 w-full rounded-lg border border-rose-200 px-4 text-sm font-semibold text-rose-700 disabled:opacity-50"
                  disabled={disabled}
                  onClick={() => govern("RETIRE")}
                  type="button"
                >
                  退役规则版本
                </button>
              </>
            ) : null}
            {!hasAvailableAction(detail, expert, governance) ? (
              <p className="rounded-lg bg-slate-50 px-3 py-3 text-sm text-slate-600">
                当前状态没有可执行操作，页面保持只读。
              </p>
            ) : null}
          </div>
        </article>
        <GovernanceBoundary />
      </aside>
    </section>
  );
}

function TextInput({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
}) {
  return (
    <label className="block text-sm font-medium text-slate-700">
      {label}
      <input
        aria-label={label}
        className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 px-3"
        min={type === "number" ? 1 : undefined}
        onChange={(event) => onChange(event.target.value)}
        type={type}
        value={value}
      />
    </label>
  );
}

function GovernanceBoundary() {
  return (
    <article className="rounded-xl border border-teal-200 bg-teal-50 p-5">
      <h2 className="font-semibold">治理边界</h2>
      <p className="mt-2 text-sm leading-6 text-slate-600">
        不显示内部人员编号、摘要密钥或原始审计载荷；延期规则不会在前端启用。
      </p>
    </article>
  );
}

function RuleStatus({ status }: { status: RuleSetStatus }) {
  const tone =
    status === "PUBLISHED"
      ? "bg-emerald-50 text-emerald-700"
      : status === "SUSPENDED" || status === "NEEDS_CORRECTION"
        ? "bg-amber-50 text-amber-800"
        : status === "RETIRED"
          ? "bg-slate-100 text-slate-600"
          : "bg-blue-50 text-blue-700";
  return (
    <span className={`w-fit rounded-full px-2.5 py-1 text-xs font-semibold ${tone}`}>{ruleStatusLabel(status)}</span>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 font-medium text-slate-800">{value}</dd>
    </div>
  );
}

function hasAvailableAction(detail: RuleSetVersionDetailDTO, expert: boolean, governance: boolean) {
  if (expert && ["DRAFT", "NEEDS_CORRECTION"].includes(detail.status)) return true;
  if (expert && detail.status === "IN_REVIEW" && detail.approval_state === "PENDING") return true;
  if (governance && detail.status === "IN_REVIEW" && detail.approval_state === "APPROVED") return true;
  if (governance && ["PUBLISHED", "SUSPENDED"].includes(detail.status)) return true;
  return false;
}

function reasonForOperation(operation: RuleGovernanceOperation, selected: RuleGovernanceReasonCode) {
  const allowed: Record<RuleGovernanceOperation, RuleGovernanceReasonCode[]> = {
    SUSPEND: [
      "MEDICAL_SAFETY_REVIEW_REQUIRED",
      "APPROVAL_EVIDENCE_INVALIDATED",
      "RULE_IMPLEMENTATION_DEFECT_CONFIRMED",
    ],
    RESUME: ["MEDICAL_SAFETY_REVIEW_CLEARED", "APPROVAL_EVIDENCE_REVALIDATED", "RULE_IMPLEMENTATION_DEFECT_REMEDIATED"],
    RETIRE: ["SUPERSEDED_BY_APPROVED_VERSION", "BASELINE_WITHDRAWN"],
  };
  return allowed[operation].includes(selected) ? selected : allowed[operation][0];
}

function ruleStatusLabel(status: string) {
  return (
    (
      {
        DRAFT: "草稿",
        IN_REVIEW: "医学审核中",
        NEEDS_CORRECTION: "需补正",
        PUBLISHED: "已生效",
        SUSPENDED: "已暂停",
        RETIRED: "已退役",
      } as Record<string, string>
    )[status] ?? "状态待核对"
  );
}

function approvalLabel(value: string) {
  return (
    ({ PENDING: "待医学审核", APPROVED: "医学审核通过", NEEDS_CORRECTION: "需补正" } as Record<string, string>)[
      value
    ] ?? "状态待核对"
  );
}

function moduleLabel(value: string) {
  return (
    (
      {
        BLOOD_PRESSURE_CARDIOVASCULAR: "血压与心血管",
        GLUCOSE_METABOLISM: "血糖代谢",
        LIPID_METABOLISM: "血脂代谢",
        WEIGHT_ABDOMINAL_OBESITY: "体重与腹型肥胖",
      } as Record<string, string>
    )[value] ?? "医学模块待核对"
  );
}

function ruleOperationLabel(value: RuleGovernanceOperation) {
  return ({ SUSPEND: "暂停规则版本", RESUME: "恢复规则版本", RETIRE: "退役规则版本" } as const)[value];
}

function formatTime(value: string | null) {
  if (!value) return "尚未生效";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待核对"
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

const correctionReasons: Array<[RuleReviewReasonCode, string]> = [
  ["RULE_CONTENT_CORRECTION_REQUIRED", "规则内容需要补正"],
  ["MEDICAL_EVIDENCE_CORRECTION_REQUIRED", "医学证据需要补正"],
  ["GOLDEN_CASE_CORRECTION_REQUIRED", "验证案例需要补正"],
  ["HIGH_RISK_SAFETY_CORRECTION_REQUIRED", "高风险安全边界需要补正"],
];

const governanceReasons: Array<[RuleGovernanceReasonCode, string]> = [
  ["MEDICAL_SAFETY_REVIEW_REQUIRED", "需要医学安全复核"],
  ["APPROVAL_EVIDENCE_INVALIDATED", "审批依据已失效"],
  ["RULE_IMPLEMENTATION_DEFECT_CONFIRMED", "规则实现缺陷已确认"],
  ["MEDICAL_SAFETY_REVIEW_CLEARED", "医学安全复核已解除"],
  ["APPROVAL_EVIDENCE_REVALIDATED", "审批依据已重新确认"],
  ["RULE_IMPLEMENTATION_DEFECT_REMEDIATED", "规则实现缺陷已修复"],
  ["SUPERSEDED_BY_APPROVED_VERSION", "已由获批版本替代"],
  ["BASELINE_WITHDRAWN", "规则基线已撤回"],
];

const primaryButton =
  "min-h-10 w-full rounded-lg bg-primary-600 px-4 text-sm font-semibold text-white disabled:opacity-50";
