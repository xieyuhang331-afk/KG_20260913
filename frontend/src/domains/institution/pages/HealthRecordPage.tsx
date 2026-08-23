import {
	Activity,
	ArrowLeft,
	CheckCircle2,
	CircleAlert,
	ClipboardCheck,
	Clock3,
	FileText,
	RefreshCw,
	ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
	getAssessmentReadiness,
	getInstitutionHealthRecord,
	getInstitutionLatestHealthIndicators,
	listInstitutionDetectionReports,
} from "../api";
import type {
	AssessmentReadiness,
	CursorPage,
	DetectionReportMetadata,
	HealthIndicatorFact,
	InstitutionHealthRecord,
} from "../types";
import {
	Feedback,
	LoadingPanel,
	secondaryButtonClassName,
} from "../受控入驻界面";
import { getSafeApiError, isUuidV7 } from "@/shared/api/slice3";

interface HealthRecordBundle {
	record: InstitutionHealthRecord;
	indicators: HealthIndicatorFact[];
	reports: CursorPage<DetectionReportMetadata>;
	readiness: AssessmentReadiness;
}

export function HealthRecordPage() {
	const { caseId = "" } = useParams();
	const [bundle, setBundle] = useState<HealthRecordBundle | null>(null);
	const [loading, setLoading] = useState(true);
	const [reportLoading, setReportLoading] = useState(false);
	const [feedback, setFeedback] = useState("");
	const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([
		null,
	]);
	const [cursorIndex, setCursorIndex] = useState(0);

	const load = useCallback(
		async (signal?: AbortSignal) => {
			if (!isUuidV7(caseId)) {
				setFeedback("服务案例标识无效，请返回客户服务接入详情后重试。");
				setLoading(false);
				return;
			}
			setLoading(true);
			try {
				const [record, reports, latest, readiness] = await Promise.all([
					getInstitutionHealthRecord(caseId, signal),
					listInstitutionDetectionReports(caseId, { limit: 20 }, signal),
					getInstitutionLatestHealthIndicators(caseId, signal),
					getAssessmentReadiness(caseId, signal),
				]);
				setBundle({ record, reports, indicators: latest.items, readiness });
				setCursorHistory([null]);
				setCursorIndex(0);
				setFeedback("");
			} catch (error) {
				if (signal?.aborted) return;
				setFeedback(getSafeApiError(error).message);
			} finally {
				if (!signal?.aborted) setLoading(false);
			}
		},
		[caseId],
	);

	useEffect(() => {
		const controller = new AbortController();
		void load(controller.signal);
		return () => controller.abort();
	}, [load]);

	async function loadReportBatch(
		cursor: string | null,
		direction: "next" | "previous",
	) {
		if (!isUuidV7(caseId)) return;
		setReportLoading(true);
		try {
			const reports = await listInstitutionDetectionReports(caseId, {
				cursor: cursor ?? undefined,
				limit: 20,
			});
			setBundle((current) => (current ? { ...current, reports } : current));
			if (direction === "next") {
				setCursorHistory((current) => [
					...current.slice(0, cursorIndex + 1),
					cursor,
				]);
				setCursorIndex((current) => current + 1);
			} else {
				setCursorIndex((current) => Math.max(0, current - 1));
			}
			setFeedback("");
		} catch (error) {
			setFeedback(getSafeApiError(error).message);
		} finally {
			setReportLoading(false);
		}
	}

	if (loading) return <LoadingPanel label="正在汇总健康档案与评估准备状态…" />;

	return (
		<main className="mx-auto w-full max-w-[1280px] space-y-5">
			<Link
				className="inline-flex items-center text-sm font-medium text-teal-700"
				to="/institution/member-enrollments"
			>
				<ArrowLeft aria-hidden="true" className="mr-1" size={16} />
				返回服务客户列表
			</Link>

			<header className="flex flex-wrap items-start justify-between gap-4">
				<div>
					<p className="text-xs font-semibold tracking-wide text-teal-700">
						客户服务 / 评估输入准备
					</p>
					<h1 className="mt-1 text-2xl font-semibold text-slate-950">
						客户健康档案与评估准备
					</h1>
					<p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
						查看当前服务范围内的档案完成度、最新指标和报告记录，确认是否具备进入健康评估的输入条件。
					</p>
				</div>
				<button
					className={secondaryButtonClassName}
					onClick={() => void load()}
					type="button"
				>
					<RefreshCw aria-hidden="true" className="mr-2" size={16} />
					刷新状态
				</button>
			</header>

			<section
				aria-label="当前健康服务上下文"
				className="grid overflow-hidden rounded-xl border border-primary-100 bg-white shadow-panel sm:grid-cols-3"
			>
				<ContextField label="当前服务客户" value="当前服务对象（已脱敏）" />
				<ContextField label="当前服务案例" value="健康管理服务" />
				<ContextField label="数据边界" value="本机构 · 当前服务范围" />
			</section>

			<Feedback message={feedback} tone="error" />
			{feedback && !bundle ? (
				<section className="rounded-xl border border-slate-200 bg-white p-6 shadow-panel">
					<h2 className="font-semibold text-slate-950">暂时无法加载健康档案</h2>
					<p className="mt-2 text-sm text-slate-600">
						请确认网络与登录状态后重新加载；系统不会把失败状态当作已完成。
					</p>
					<button
						className={`${secondaryButtonClassName} mt-4`}
						onClick={() => void load()}
						type="button"
					>
						重新加载
					</button>
				</section>
			) : null}

			{bundle ? (
				<>
					<StatusRail bundle={bundle} />
					<section className="flex items-start gap-3 rounded-xl border border-primary-100 bg-primary-50 px-5 py-4 text-sm text-primary-600">
						<CircleAlert
							aria-hidden="true"
							className="mt-0.5 shrink-0"
							size={18}
						/>
						<div>
							<p className="font-semibold">输入条件齐备不等于已产生评估结果</p>
							<p className="mt-1 leading-6 text-slate-600">
								本页只汇总评估输入准备情况，不展示风险结论、健康方案或诊断建议。
							</p>
						</div>
					</section>
					<section className="grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
						<div className="space-y-5">
							<IndicatorPanel
								indicators={bundle.indicators}
								states={bundle.record.indicator_states}
							/>
							<ReportPanel
								cursorIndex={cursorIndex}
								loading={reportLoading}
								onNext={() =>
									bundle.reports.next_cursor
										? void loadReportBatch(bundle.reports.next_cursor, "next")
										: undefined
								}
								onPrevious={() =>
									void loadReportBatch(
										cursorHistory[cursorIndex - 1] ?? null,
										"previous",
									)
								}
								page={bundle.reports}
							/>
						</div>
						<aside className="space-y-5">
							<RecordSummaryPanel record={bundle.record} />
							<ReadinessPanel readiness={bundle.readiness} />
							<article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
								<div className="flex items-center gap-2">
									<ShieldCheck
										aria-hidden="true"
										className="text-teal-700"
										size={19}
									/>
									<h2 className="font-semibold text-slate-950">查看边界</h2>
								</div>
								<ul className="mt-4 space-y-2 text-sm leading-6 text-slate-600">
									<li>仅展示当前机构与当前服务案例获批范围内的摘要。</li>
									<li>报告仅展示元数据，不提供原件或附件访问入口。</li>
									<li>本页不提供健康数据写入、核验、争议或修正。</li>
								</ul>
							</article>
						</aside>
					</section>
				</>
			) : null}
		</main>
	);
}

function ContextField({ label, value }: { label: string; value: string }) {
	return (
		<div className="border-b border-primary-50 px-5 py-4 last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0">
			<p className="text-xs font-medium text-slate-500">{label}</p>
			<p className="mt-1 text-sm font-semibold text-slate-950">{value}</p>
		</div>
	);
}

function RecordSummaryPanel({ record }: { record: InstitutionHealthRecord }) {
	const complete = record.profile_completion_status === "COMPLETE";
	return (
		<article className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
			<div className="flex items-center gap-2">
				<ClipboardCheck
					aria-hidden="true"
					className="text-teal-700"
					size={19}
				/>
				<h2 className="font-semibold text-slate-950">档案摘要</h2>
			</div>
			<div
				className={`mt-4 rounded-lg px-3 py-3 text-sm font-semibold ${complete ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}
			>
				{complete ? "档案已完整" : "档案仍有缺失模块"}
			</div>
			{!complete ? (
				<ul className="mt-3 space-y-2 text-sm text-slate-700">
					{record.missing_section_codes.map((code) => (
						<li
							className="rounded-lg border border-amber-100 bg-amber-50/60 px-3 py-2"
							key={code}
						>
							{missingSectionLabel(code)}
						</li>
					))}
				</ul>
			) : null}
			<dl className="mt-4 grid grid-cols-2 gap-3 border-t border-slate-100 pt-4 text-sm">
				<SummaryField
					label="服务指标"
					value={`${record.indicator_codes.length} 项`}
				/>
				<SummaryField
					label="报告记录"
					value={`${record.report_metadata_count} 份`}
				/>
				<div className="col-span-2">
					<SummaryField
						label="最近更新"
						value={formatTime(record.updated_at)}
					/>
				</div>
			</dl>
		</article>
	);
}

function StatusRail({ bundle }: { bundle: HealthRecordBundle }) {
	const complete = bundle.record.profile_completion_status === "COMPLETE";
	const current = bundle.readiness.projection_status === "CURRENT";
	const ready = bundle.readiness.status === "ASSESSMENT_READY";
	const steps = [
		{
			label: "档案摘要",
			value: complete ? "档案已完整" : "档案待补充",
			done: complete,
		},
		{
			label: "服务指标",
			value: `${bundle.indicators.length} 项最新数据`,
			done: bundle.indicators.length > 0,
		},
		{
			label: "数据汇总",
			value: current ? "已同步" : "等待同步",
			done: current,
		},
		{
			label: "评估条件",
			value: readinessTitle(bundle.readiness.status),
			done: ready,
		},
	];
	return (
		<section
			aria-label="评估输入链"
			className="grid overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel sm:grid-cols-2 xl:grid-cols-4"
		>
			{steps.map((step, index) => (
				<div
					className="relative border-slate-100 px-5 py-4 sm:border-r"
					key={step.label}
				>
					<div className="flex items-center gap-2 text-xs font-semibold text-slate-500">
						<span
							className={`flex h-6 w-6 items-center justify-center rounded-full ${step.done ? "bg-teal-100 text-teal-700" : "bg-amber-100 text-amber-700"}`}
						>
							{step.done ? (
								<CheckCircle2 aria-hidden="true" size={15} />
							) : (
								<Clock3 aria-hidden="true" size={15} />
							)}
						</span>
						{index + 1}. {step.label}
					</div>
					<p className="mt-2 font-semibold text-slate-950">{step.value}</p>
				</div>
			))}
		</section>
	);
}

function IndicatorPanel({
	indicators,
	states,
}: {
	indicators: HealthIndicatorFact[];
	states: Record<string, string>;
}) {
	return (
		<article className="rounded-xl border border-slate-200 bg-white shadow-panel">
			<div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
				<div>
					<div className="flex items-center gap-2">
						<Activity aria-hidden="true" className="text-teal-700" size={20} />
						<h2 className="font-semibold text-slate-950">最新健康指标</h2>
					</div>
					<p className="mt-1 text-xs text-slate-500">
						由服务端按当前服务范围筛选；页面不自行计算有效性或 BMI。
					</p>
				</div>
				<span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
					{indicators.length} 项
				</span>
			</div>
			{indicators.length ? (
				<div className="grid gap-px bg-slate-100 sm:grid-cols-2 xl:grid-cols-3">
					{indicators.map((item) => (
						<div className="bg-white p-5" key={item.fact_ref}>
							<div className="flex items-start justify-between gap-3">
								<p className="text-sm font-medium text-slate-600">
									{indicatorLabel(item.indicator_code)}
								</p>
								<StateBadge
									state={states[item.indicator_code] ?? item.verification_state}
								/>
							</div>
							<p className="mt-3 text-2xl font-semibold tabular-nums text-slate-950">
								{String(item.value)}{" "}
								<span className="text-sm font-medium text-slate-500">
									{unitLabel(item.unit)}
								</span>
							</p>
							<p className="mt-2 text-xs text-slate-400">
								{item.indicator_code === "bmi"
									? "服务端已计算"
									: `测量于 ${formatTime(item.measured_at)}`}
							</p>
						</div>
					))}
				</div>
			) : (
				<p className="px-5 py-10 text-center text-sm text-slate-500">
					当前服务范围内暂无可展示的最新指标。
				</p>
			)}
		</article>
	);
}

function ReportPanel({
	page,
	cursorIndex,
	loading,
	onNext,
	onPrevious,
}: {
	page: CursorPage<DetectionReportMetadata>;
	cursorIndex: number;
	loading: boolean;
	onNext: () => void;
	onPrevious: () => void;
}) {
	return (
		<article className="rounded-xl border border-slate-200 bg-white shadow-panel">
			<div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
				<div>
					<div className="flex items-center gap-2">
						<FileText aria-hidden="true" className="text-teal-700" size={19} />
						<h2 className="font-semibold text-slate-950">检测报告记录</h2>
					</div>
					<p className="mt-1 text-xs text-slate-500">
						仅展示报告类型、时间和处理状态，不提供报告原件。
					</p>
				</div>
				<div className="flex gap-2">
					<button
						className={secondaryButtonClassName}
						disabled={loading || cursorIndex === 0}
						onClick={onPrevious}
						type="button"
					>
						上一批
					</button>
					<button
						className={secondaryButtonClassName}
						disabled={loading || !page.next_cursor}
						onClick={onNext}
						type="button"
					>
						下一批
					</button>
				</div>
			</div>
			{page.items.length ? (
				<div className="divide-y divide-slate-100">
					{page.items.map((report) => (
						<div
							className="grid gap-3 px-5 py-4 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:items-center"
							key={report.report_id}
						>
							<div>
								<p className="font-medium text-slate-950">
									{reportTypeLabel(report.report_type)}
								</p>
								<p className="mt-1 text-xs text-slate-500">
									检测于 {formatTime(report.measured_at)}
								</p>
							</div>
							<span className="text-sm text-slate-600">
								{report.attachment_count} 个附件
							</span>
							<span className="justify-self-start rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 sm:justify-self-end">
								{reportStatusLabel(report.status)}
							</span>
						</div>
					))}
				</div>
			) : (
				<p className="px-5 py-10 text-center text-sm text-slate-500">
					当前批次没有检测报告记录，可等待服务客户完成受控报告提交。
				</p>
			)}
		</article>
	);
}

function ReadinessPanel({ readiness }: { readiness: AssessmentReadiness }) {
	const ready = readiness.status === "ASSESSMENT_READY";
	const details = [
		...readiness.missing_indicator_codes.map(
			(code) => `缺少：${indicatorLabel(code)}`,
		),
		...readiness.expired_indicator_codes.map(
			(code) => `已过期：${indicatorLabel(code)}`,
		),
		...readiness.disputed_indicator_codes.map(
			(code) => `待确认：${indicatorLabel(code)}`,
		),
	];
	if (readiness.status === "DATA_SYNC_PENDING")
		details.push("系统正在汇总最新数据，请稍后刷新。 ");
	return (
		<article
			className={`rounded-xl border p-5 shadow-panel ${ready ? "border-teal-200 bg-teal-50/70" : "border-amber-200 bg-amber-50/70"}`}
		>
			<div className="flex items-start gap-3">
				<span
					className={`rounded-lg p-2 ${ready ? "bg-teal-100 text-teal-700" : "bg-amber-100 text-amber-700"}`}
				>
					{ready ? (
						<ClipboardCheck aria-hidden="true" size={20} />
					) : (
						<CircleAlert aria-hidden="true" size={20} />
					)}
				</span>
				<div>
					<p className="text-xs font-semibold tracking-wide text-slate-500">
						评估准备状态
					</p>
					<h2 className="mt-1 text-lg font-semibold text-slate-950">
						{readinessTitle(readiness.status)}
					</h2>
				</div>
			</div>
			{ready ? (
				<p className="mt-4 rounded-lg bg-white/80 p-3 text-sm leading-6 text-slate-700">
					仅表示评估所需输入条件齐备，不代表已经产生评估结果、风险结论或健康方案。
				</p>
			) : details.length ? (
				<ul className="mt-4 space-y-2 text-sm text-slate-700">
					{details.map((detail) => (
						<li className="rounded-lg bg-white/80 px-3 py-2" key={detail}>
							{detail}
						</li>
					))}
				</ul>
			) : (
				<p className="mt-4 text-sm text-slate-700">
					当前状态尚未具备可执行的评估输入，请稍后刷新。
				</p>
			)}
			<dl className="mt-4 grid gap-3 border-t border-slate-200/70 pt-4 text-sm sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
				<SummaryField
					label="数据截至"
					value={formatOptionalTime(readiness.data_as_of)}
				/>
				<SummaryField
					label="状态生成"
					value={formatOptionalTime(readiness.generated_at)}
				/>
			</dl>
		</article>
	);
}

function StateBadge({ state }: { state: string }) {
	const disputed = state === "DISPUTED";
	return (
		<span
			className={`rounded-full px-2 py-1 text-[11px] font-semibold ${disputed ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700"}`}
		>
			{disputed ? "待确认" : state === "VERIFIED" ? "已核验" : "已记录"}
		</span>
	);
}

function SummaryField({ label, value }: { label: string; value: string }) {
	return (
		<div>
			<dt className="text-xs text-slate-500">{label}</dt>
			<dd className="mt-1 font-medium text-slate-800">{value}</dd>
		</div>
	);
}

function readinessTitle(status: AssessmentReadiness["status"]) {
	return (
		{
			DATA_INSUFFICIENT: "资料尚不充足",
			DATA_SYNC_PENDING: "数据同步中",
			DISPUTED: "存在待确认指标",
			ASSESSMENT_READY: "已具备评估条件",
		} as const
	)[status];
}

function indicatorLabel(code: string) {
	return (
		(
			{
				height: "身高",
				weight: "体重",
				waist: "腰围",
				bmi: "BMI",
				systolic_bp: "收缩压",
				diastolic_bp: "舒张压",
				heart_rate: "心率",
				fasting_glucose: "空腹血糖",
				postprandial_glucose_2h: "餐后2小时血糖",
				hba1c: "糖化血红蛋白",
				total_cholesterol: "总胆固醇",
				triglyceride: "甘油三酯",
				hdl_c: "高密度脂蛋白胆固醇",
				ldl_c: "低密度脂蛋白胆固醇",
				uric_acid: "尿酸",
				spo2: "血氧饱和度",
				bone_density_t_score: "骨密度T值",
			} as Record<string, string>
		)[code] ?? "其他服务指标"
	);
}

function reportTypeLabel(type: string) {
	return (
		(
			{
				LAB_REPORT: "检验报告",
				IMAGING_REPORT: "影像报告",
				PHYSICAL_EXAM: "年度体检报告",
				OTHER: "其他检测报告",
			} as Record<string, string>
		)[type] ?? "检测报告"
	);
}

function missingSectionLabel(code: string) {
	return (
		({ HEALTH_PROFILE: "基础健康档案待补充" } as Record<string, string>)[
			code
		] ?? "档案模块待补充"
	);
}

function reportStatusLabel(status: string) {
	return (
		(
			{
				READY: "已归档",
				ACTIVE: "有效",
				CLEAN: "安全可用",
				SUPERSEDED: "已有更新版本",
			} as Record<string, string>
		)[status] ?? "已记录"
	);
}

function unitLabel(unit: string) {
	return unit === "kg/m2" ? "kg/m²" : unit;
}

function formatTime(value: string) {
	const date = new Date(value);
	return Number.isNaN(date.getTime())
		? "时间待确认"
		: new Intl.DateTimeFormat("zh-CN", {
				dateStyle: "short",
				timeStyle: "short",
			}).format(date);
}

function formatOptionalTime(value: string | null) {
	return value ? formatTime(value) : "尚未生成";
}
