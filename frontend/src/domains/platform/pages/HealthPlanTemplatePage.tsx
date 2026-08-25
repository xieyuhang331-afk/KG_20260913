import { FileLock2, RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  createHealthPlanTemplate,
  getHealthPlanTemplate,
  getSafeSlice6Error,
  listHealthPlanTemplates,
  publishHealthPlanTemplate,
  retireHealthPlanTemplate,
  type HealthPlanTemplateCreateInput,
  type HealthPlanTemplateDTO,
  type ModuleCode,
} from "@/shared/api/slice6";
import { createIdempotencyKey, isUuidV7, toUuidV7 } from "@/shared/api/slice3";
import { Feedback, LoadingPanel, secondaryButtonClassName } from "@/domains/institution/受控入驻界面";

export function HealthPlanTemplatePage() {
  const { templateVersionId = "" } = useParams();
  const [templates, setTemplates] = useState<HealthPlanTemplateDTO[]>([]);
  const [detail, setDetail] = useState<HealthPlanTemplateDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [feedbackTone, setFeedbackTone] = useState<"success" | "error">("error");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (templateVersionId) {
        if (!isUuidV7(templateVersionId)) throw new Error("UUID_V7_REQUIRED");
        setDetail(await getHealthPlanTemplate(toUuidV7(templateVersionId)));
      } else {
        setTemplates((await listHealthPlanTemplates({ limit: 20 })).items);
      }
      setFeedback("");
    } catch (error) {
      setFeedbackTone("error");
      setFeedback(getSafeSlice6Error(error).message);
    } finally {
      setLoading(false);
    }
  }, [templateVersionId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function changeState(action: "publish" | "retire") {
    if (
      !detail ||
      !window.confirm(
        action === "publish" ? "确认发布该模板版本？发布后医学锁定字段不可修改。" : "确认退役该模板版本？",
      )
    )
      return;
    setSubmitting(true);
    try {
      const result =
        action === "publish"
          ? await publishHealthPlanTemplate(detail.template_version_id, detail.version, createIdempotencyKey())
          : await retireHealthPlanTemplate(detail.template_version_id, detail.version, createIdempotencyKey());
      setDetail(result);
      setFeedbackTone("success");
      setFeedback(action === "publish" ? "模板已发布。" : "模板已退役。");
    } catch (error) {
      const safe = getSafeSlice6Error(error);
      if (safe.refreshRequired) await load();
      setFeedbackTone("error");
      setFeedback(safe.message);
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <LoadingPanel label="正在加载方案模板…" />;

  return (
    <main className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-wide text-teal-700">医学内容治理 / 版本控制</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-950">方案模板治理</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
            治理结构化健康管理模板的版本、发布与退役状态，不包含自由医学内容。
          </p>
        </div>
        <button className={secondaryButtonClassName} onClick={() => void load()} type="button">
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新
        </button>
      </header>
      <Feedback message={feedback} tone={feedbackTone} />
      {detail ? (
        <TemplateDetail detail={detail} disabled={submitting} onStateChange={changeState} />
      ) : (
        <TemplateList onCreated={setDetail} templates={templates} />
      )}
    </main>
  );
}

function TemplateList({
  templates,
  onCreated,
}: {
  templates: HealthPlanTemplateDTO[];
  onCreated: (template: HealthPlanTemplateDTO) => void;
}) {
  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
      <article className="rounded-xl border border-slate-200 bg-white shadow-panel">
        <div className="border-b border-slate-100 px-5 py-4">
          <h2 className="font-semibold">模板版本</h2>
        </div>
        {templates.length ? (
          templates.map((item) => (
            <Link
              className="grid gap-3 border-b border-slate-100 px-5 py-4 last:border-0 sm:grid-cols-[minmax(0,1fr)_auto]"
              key={item.template_version_id}
              to={`/platform/health-plan-templates/${item.template_version_id}`}
            >
              <div>
                <p className="font-medium text-slate-950">{templateLabel(item.template_code)}</p>
                <p className="mt-1 text-xs text-slate-500">版本 {item.version_no}</p>
              </div>
              <span className="self-center rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold">
                {templateStatusLabel(item.status)}
              </span>
            </Link>
          ))
        ) : (
          <p className="px-5 py-10 text-center text-sm text-slate-500">
            当前没有模板版本。创建合同冻结后可在此建立首个草稿。
          </p>
        )}
      </article>
      <TemplateCreateForm onCreated={onCreated} />
    </section>
  );
}

function TemplateDetail({
  detail,
  disabled,
  onStateChange,
}: {
  detail: HealthPlanTemplateDTO;
  disabled: boolean;
  onStateChange: (action: "publish" | "retire") => void;
}) {
  return (
    <section className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
      <article className="rounded-xl border border-slate-200 bg-white p-6 shadow-panel">
        <h2 className="text-xl font-semibold">{templateLabel(detail.template_code)}</h2>
        <p className="mt-1 text-sm text-slate-500">
          {detail.template_code} · 版本 {detail.version_no}
        </p>
        <div className="mt-5 rounded-xl border border-teal-200 bg-teal-50 p-4">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden="true" size={19} />
            <h3 className="font-semibold">医学锁定字段只读</h3>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {detail.applicable_modules.map((code) => (
              <span className="rounded-full bg-white px-3 py-1 text-xs font-medium" key={code}>
                {moduleLabel(code)}
              </span>
            ))}
          </div>
        </div>
      </article>
      <aside className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
        <p className="text-xs text-slate-500">当前状态</p>
        <p className="mt-1 font-semibold">{templateStatusLabel(detail.status)}</p>
        <div className="mt-5 grid gap-2">
          {detail.status === "DRAFT" ? (
            <button
              className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
              disabled={disabled}
              onClick={() => void onStateChange("publish")}
              type="button"
            >
              发布模板
            </button>
          ) : null}
          {detail.status === "PUBLISHED" ? (
            <button
              className="rounded-lg border border-rose-200 px-4 py-2 text-sm font-semibold text-rose-700 disabled:opacity-60"
              disabled={disabled}
              onClick={() => void onStateChange("retire")}
              type="button"
            >
              退役模板
            </button>
          ) : null}
        </div>
      </aside>
    </section>
  );
}

function templateStatusLabel(status: string) {
  return ({ DRAFT: "草稿", PUBLISHED: "已发布", RETIRED: "已退役" } as Record<string, string>)[status] ?? "状态待核对";
}

function TemplateCreateForm({ onCreated }: { onCreated: (template: HealthPlanTemplateDTO) => void }) {
  const [templateCode, setTemplateCode] = useState("");
  const [moduleCode, setModuleCode] = useState<ModuleCode>("WEIGHT_ABDOMINAL_OBESITY");
  const [goalCodes, setGoalCodes] = useState("");
  const [stageCodes, setStageCodes] = useState("");
  const [milestoneCodes, setMilestoneCodes] = useState("");
  const [sopCodes, setSopCodes] = useState("");
  const [contraindicationCodes, setContraindicationCodes] = useState("");
  const [userMessageCodes, setUserMessageCodes] = useState("");
  const [therapistActionCodes, setTherapistActionCodes] = useState("");
  const [medicalApprovalRef, setMedicalApprovalRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    const input: HealthPlanTemplateCreateInput = {
      template_code: templateCode.trim().toUpperCase(),
      applicable_modules: [moduleCode],
      goals_by_module: { [moduleCode]: structuredCodes(goalCodes) },
      stage_codes: structuredCodes(stageCodes),
      milestone_codes: structuredCodes(milestoneCodes),
      sop_codes: structuredCodes(sopCodes),
      contraindication_codes: structuredCodes(contraindicationCodes),
      user_message_codes: structuredCodes(userMessageCodes),
      therapist_action_codes: structuredCodes(therapistActionCodes),
      medical_approval_ref: medicalApprovalRef.trim(),
    };
    if (
      !input.template_code ||
      !input.medical_approval_ref ||
      Object.values(input).some((value) => Array.isArray(value) && value.length === 0)
    ) {
      setFeedback("请完整填写模板编码、结构化内容和医学审批依据。");
      return;
    }
    if (!window.confirm("确认创建该结构化模板草稿？")) return;
    setSubmitting(true);
    try {
      onCreated(await createHealthPlanTemplate(input, createIdempotencyKey()));
      setFeedback("");
    } catch (error) {
      setFeedback(getSafeSlice6Error(error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <aside className="rounded-xl border border-primary-100 bg-white p-5 shadow-panel">
      <div className="flex items-center gap-2">
        <FileLock2 aria-hidden="true" className="text-primary-600" size={20} />
        <h2 className="font-semibold">创建模板草稿</h2>
      </div>
      <p className="mt-3 rounded-lg bg-teal-50 px-3 py-2 text-sm font-semibold text-teal-800">医学锁定字段只读</p>
      <form className="mt-4 space-y-3" onSubmit={(event) => void submit(event)}>
        <TextField label="模板编码" onChange={setTemplateCode} value={templateCode} />
        <label className="block text-sm font-medium text-slate-700">
          适用模块
          <select
            className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
            onChange={(event) => setModuleCode(event.target.value as ModuleCode)}
            value={moduleCode}
          >
            <option value="WEIGHT_ABDOMINAL_OBESITY">体重与腹型肥胖</option>
            <option value="BLOOD_PRESSURE_CARDIOVASCULAR">血压与心血管</option>
            <option value="GLUCOSE_METABOLISM">血糖代谢</option>
            <option value="LIPID_METABOLISM">血脂代谢</option>
          </select>
        </label>
        <TextField label="模块目标编码" onChange={setGoalCodes} value={goalCodes} />
        <TextField label="阶段编码" onChange={setStageCodes} value={stageCodes} />
        <TextField label="里程碑编码" onChange={setMilestoneCodes} value={milestoneCodes} />
        <TextField label="服务规范编码" onChange={setSopCodes} value={sopCodes} />
        <TextField label="禁忌编码" onChange={setContraindicationCodes} value={contraindicationCodes} />
        <TextField label="用户提示编码" onChange={setUserMessageCodes} value={userMessageCodes} />
        <TextField label="健管师动作编码" onChange={setTherapistActionCodes} value={therapistActionCodes} />
        <TextField label="医学审批依据" onChange={setMedicalApprovalRef} value={medicalApprovalRef} />
        {feedback ? (
          <p className="text-sm text-rose-700" role="alert">
            {feedback}
          </p>
        ) : null}
        <button
          className="w-full rounded-lg bg-primary-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-60"
          disabled={submitting}
          type="submit"
        >
          {submitting ? "创建中…" : "创建模板草稿"}
        </button>
      </form>
    </aside>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="block text-sm font-medium text-slate-700">
      {label}
      <input
        className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
    </label>
  );
}

function structuredCodes(value: string) {
  return value
    .split(/[\s,，]+/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean);
}

function moduleLabel(code: string) {
  return (
    (
      {
        BLOOD_PRESSURE_CARDIOVASCULAR: "血压与心血管",
        GLUCOSE_METABOLISM: "血糖代谢",
        LIPID_METABOLISM: "血脂代谢",
        WEIGHT_ABDOMINAL_OBESITY: "体重与腹型肥胖",
      } as Record<string, string>
    )[code] ?? "结构化模块"
  );
}

function templateLabel(code: string) {
  return ({ METABOLIC_FOUNDATION: "代谢健康基础方案" } as Record<string, string>)[code] ?? "结构化健康方案模板";
}
