import {
	CheckCircle2,
	FileLock2,
	RefreshCw,
	Search,
	ShieldAlert,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
	ConfirmDialog,
	EmptyPanel,
	Feedback,
	LoadingPanel,
	applicationStatusClassName,
	applicationStatusLabel,
	fieldClassName,
	onboardingErrorMessage,
	primaryButtonClassName,
	secondaryButtonClassName,
} from "@/domains/institution/受控入驻界面";
import { isApiError } from "@/shared/api/errors";
import {
	decideInstitutionReview,
	fetchPrivateFileContent,
	getInstitutionReviewDetail,
	listInstitutionReviews,
	requestPrivateFileAccess,
	type InstitutionReviewDecisionPayload,
	type InstitutionReviewView,
} from "../api";

const correctionOptions = [
	["credit_code", "统一社会信用代码"],
	["legal_representative_name", "法定代表人"],
	["registered_address", "注册地址"],
	["service_address", "服务地址"],
	["contact_name", "联系人"],
	["contact_phone", "联系电话"],
	["contact_email", "联系邮箱"],
	["service_tags", "服务标签"],
	["business_license", "营业执照"],
	["medical_institution_license", "医疗机构执业许可证"],
] as const;

const draftFields = correctionOptions.filter(
	([key]) => !key.endsWith("license"),
);
type PendingDecision = InstitutionReviewDecisionPayload["decision"] | null;

export function InstitutionReviewPage() {
	const passwordRef = useRef<HTMLInputElement>(null);
	const requestGeneration = useRef(0);
	const [rows, setRows] = useState<InstitutionReviewView[]>([]);
	const [detail, setDetail] = useState<InstitutionReviewView | null>(null);
	const [loadingList, setLoadingList] = useState(true);
	const [loadingDetail, setLoadingDetail] = useState(false);
	const [listError, setListError] = useState("");
	const [message, setMessage] = useState("");
	const [tone, setTone] = useState<"success" | "error">("success");
	const [selectedCorrectionFields, setSelectedCorrectionFields] = useState<
		string[]
	>([]);
	const [busy, setBusy] = useState(false);
	const [pendingDecision, setPendingDecision] = useState<PendingDecision>(null);

	const load = useCallback(async () => {
		setLoadingList(true);
		setListError("");
		try {
			setRows(await listInstitutionReviews());
		} catch (error) {
			setListError(onboardingErrorMessage(error, "审核队列读取失败，请重试。"));
		} finally {
			setLoadingList(false);
		}
	}, []);

	useEffect(() => {
		void load();
		return () => {
			requestGeneration.current += 1;
		};
	}, [load]);

	async function openDetail(id: string) {
		const generation = ++requestGeneration.current;
		setDetail(null);
		setSelectedCorrectionFields([]);
		if (passwordRef.current) passwordRef.current.value = "";
		setLoadingDetail(true);
		setMessage("");
		try {
			const value = await getInstitutionReviewDetail(id);
			if (generation === requestGeneration.current) setDetail(value);
		} catch (error) {
			if (generation === requestGeneration.current) {
				setTone("error");
				setMessage(onboardingErrorMessage(error, "审核详情读取失败，请重试。"));
			}
		} finally {
			if (generation === requestGeneration.current) setLoadingDetail(false);
		}
	}

	async function viewMaterial(fileId: string) {
		if (busy) return;
		const password = passwordRef.current?.value ?? "";
		if (passwordRef.current) passwordRef.current.value = "";
		if (!password) {
			setTone("error");
			setMessage("请先输入当前账户密码，再查看材料。 ");
			passwordRef.current?.focus();
			return;
		}
		setBusy(true);
		setMessage("");
		try {
			const access = await requestPrivateFileAccess(fileId, password);
			const blob = await fetchPrivateFileContent(access.access_path);
			const objectUrl = URL.createObjectURL(blob);
			window.open(objectUrl, "_blank", "noopener,noreferrer");
			window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
			setTone("success");
			setMessage("材料已在新窗口打开，临时查看地址将在 60 秒后清理。 ");
		} catch (error) {
			setTone("error");
			setMessage(
				onboardingErrorMessage(error, "材料读取失败，请重新验证密码。"),
			);
		} finally {
			setBusy(false);
		}
	}

	function prepareDecision(decision: Exclude<PendingDecision, null>) {
		if (
			decision === "NEEDS_CORRECTION" &&
			selectedCorrectionFields.length === 0
		) {
			setTone("error");
			setMessage("请至少选择一个需要补正的字段或材料。 ");
			return;
		}
		setPendingDecision(decision);
	}

	async function confirmDecision() {
		if (!detail || !pendingDecision || busy) return;
		const decision = pendingDecision;
		const applicationId = detail.application_id;
		setBusy(true);
		setMessage("");
		try {
			await decideInstitutionReview(
				applicationId,
				{
					decision,
					expected_version: detail.version,
					correction_fields:
						decision === "NEEDS_CORRECTION" ? selectedCorrectionFields : [],
					reason_code:
						decision === "APPROVED"
							? null
							: decision === "NEEDS_CORRECTION"
								? "CORRECTION_REQUIRED"
								: "REJECTED_BY_PLATFORM",
				},
				crypto.randomUUID(),
			);
			requestGeneration.current += 1;
			setDetail(null);
			setSelectedCorrectionFields([]);
			setPendingDecision(null);
			setTone("success");
			setMessage(decisionSuccessMessage(decision));
			await load();
		} catch (error) {
			setPendingDecision(null);
			setTone("error");
			setMessage(
				onboardingErrorMessage(error, "审核决定提交失败，请确认当前状态。"),
			);
			if (isApiError(error) && error.status === 409) {
				await load();
				await openDetail(applicationId);
			}
		} finally {
			setBusy(false);
		}
	}

	const materials = detail?.materials ?? [];
	return (
		<main className="space-y-5">
			<header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
				<div>
					<p className="text-xs font-semibold tracking-[0.16em] text-teal-700">
						APPLICATION REVIEW
					</p>
					<h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">
						机构入驻审核
					</h1>
					<p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
						核对当前修订与安全扫描通过的材料，再决定补正、拒绝或批准。
					</p>
				</div>
				<button
					className={secondaryButtonClassName}
					disabled={loadingList || busy}
					onClick={() => void load()}
					type="button"
				>
					<RefreshCw aria-hidden="true" className="mr-2" size={16} />
					刷新队列
				</button>
			</header>

			<Feedback message={message} tone={tone} />
			<div className="grid items-start gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
				<section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
					<div className="border-b border-slate-100 px-5 py-4">
						<h2 className="font-semibold text-slate-950">待处理申请</h2>
						<p className="mt-1 text-xs text-slate-500">
							选择申请后在右侧完成审核
						</p>
					</div>
					{loadingList ? (
						<div className="p-4">
							<LoadingPanel label="正在读取审核队列…" />
						</div>
					) : listError ? (
						<div className="p-4">
							<EmptyPanel
								action={
									<button
										className={secondaryButtonClassName}
										onClick={() => void load()}
										type="button"
									>
										重试
									</button>
								}
								description={listError}
								title="审核队列暂时不可用"
							/>
						</div>
					) : rows.length === 0 ? (
						<div className="p-4">
							<EmptyPanel
								description="当前没有待审核申请。稍后刷新可获取新提交。"
								title="队列已处理完"
							/>
						</div>
					) : (
						<ul className="divide-y divide-slate-100">
							{rows.map((row) => (
								<li key={row.application_id}>
									<button
										aria-label="查看详情"
										aria-pressed={detail?.application_id === row.application_id}
										className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left transition hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-500 aria-pressed:bg-teal-50"
										disabled={busy}
										onClick={() => void openDetail(row.application_id)}
										type="button"
									>
										<span className="min-w-0">
											<span className="block truncate text-sm font-semibold text-slate-950">
												申请 {shortId(row.application_id)}
											</span>
											<span className="mt-1 block text-xs text-slate-500">
												{row.submitted_at
													? formatTime(row.submitted_at)
													: "等待提交时间"}
											</span>
										</span>
										<span
											className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${applicationStatusClassName(row.status)}`}
										>
											{applicationStatusLabel(row.status)}
										</span>
									</button>
								</li>
							))}
						</ul>
					)}
				</section>

				<section className="min-w-0">
					{loadingDetail ? (
						<LoadingPanel label="正在安全读取申请详情…" />
					) : !detail ? (
						<EmptyPanel
							action={
								<Search
									aria-hidden="true"
									className="mx-auto text-slate-400"
									size={24}
								/>
							}
							description="从左侧选择一条申请，核对资料、证照和当前版本。"
							title="请选择申请"
						/>
					) : (
						<div className="space-y-5">
							<section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
								<div className="flex flex-wrap items-start justify-between gap-3">
									<div>
										<p className="text-xs font-semibold text-slate-400">
											申请编号 {shortId(detail.application_id)}
										</p>
										<h2 className="mt-2 text-xl font-semibold text-slate-950">
											申请详情
										</h2>
									</div>
									<span
										className={`rounded-full px-3 py-1.5 text-sm font-semibold ring-1 ${applicationStatusClassName(detail.status)}`}
									>
										{applicationStatusLabel(detail.status)}
									</span>
								</div>
								<dl className="mt-5 grid gap-x-6 gap-y-4 sm:grid-cols-2">
									{draftFields.map(([key, label]) =>
										detail.draft?.[key] == null ? null : (
											<div key={key}>
												<dt className="text-xs text-slate-500">{label}</dt>
												<dd className="mt-1 break-words text-sm font-medium text-slate-950">
													{Array.isArray(detail.draft[key])
														? (detail.draft[key] as string[]).join("、")
														: String(detail.draft[key])}
												</dd>
											</div>
										),
									)}
								</dl>
							</section>

							<section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
								<div className="flex items-start gap-3">
									<span className="rounded-lg bg-blue-50 p-2 text-blue-700">
										<FileLock2 aria-hidden="true" size={19} />
									</span>
									<div>
										<h2 className="font-semibold text-slate-950">
											受控材料查看
										</h2>
										<p className="mt-1 text-xs leading-5 text-slate-500">
											每次查看都需要当前账户密码。密码和临时访问地址不保存。
										</p>
									</div>
								</div>
								<label className="mt-4 block max-w-md text-sm font-medium text-slate-700">
									当前账户密码
									<input
										autoComplete="current-password"
										className={fieldClassName}
										ref={passwordRef}
										type="password"
									/>
								</label>
								<div className="mt-4 space-y-3">
									{materials.length ? (
										materials.map((material) => (
											<div
												className="flex flex-col gap-3 rounded-lg border border-slate-200 p-4 sm:flex-row sm:items-center sm:justify-between"
												key={material.license_type}
											>
												<div>
													<p className="text-sm font-semibold text-slate-950">
														{licenseLabel(material.license_type)}
													</p>
													<p className="mt-1 text-xs text-slate-500">
														扫描状态：{scanStatusLabel(material.status)}
													</p>
												</div>
												<button
													aria-label="受控查看材料"
													className={secondaryButtonClassName}
													disabled={busy || material.status !== "CLEAN"}
													onClick={() => void viewMaterial(material.file_id)}
													type="button"
												>
													受控查看
												</button>
											</div>
										))
									) : (
										<p className="text-sm text-slate-500">
											当前申请没有可查看材料。
										</p>
									)}
								</div>
							</section>

							<section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel">
								<div className="flex items-start gap-3">
									<span className="rounded-lg bg-amber-50 p-2 text-amber-700">
										<ShieldAlert aria-hidden="true" size={19} />
									</span>
									<div>
										<h2 className="font-semibold text-slate-950">审核决定</h2>
										<p className="mt-1 text-xs leading-5 text-slate-500">
											冲突后只刷新最新状态，不自动重放决定。所有操作均需二次确认。
										</p>
									</div>
								</div>
								<fieldset className="mt-4 rounded-lg border border-slate-200 p-4">
									<legend className="px-1 text-sm font-semibold text-slate-700">
										需要补正的字段
									</legend>
									<div className="grid gap-2 pt-2 sm:grid-cols-2 lg:grid-cols-3">
										{correctionOptions.map(([field, label]) => (
											<label
												className="flex items-center gap-2 rounded-lg px-2 py-2 text-sm text-slate-700 hover:bg-slate-50"
												key={field}
											>
												<input
													aria-label={`${label}需补正`}
													checked={selectedCorrectionFields.includes(field)}
													className="h-4 w-4 accent-teal-700"
													disabled={busy}
													onChange={(event) =>
														setSelectedCorrectionFields((current) =>
															event.target.checked
																? [...current, field]
																: current.filter((value) => value !== field),
														)
													}
													type="checkbox"
												/>
												{label}
											</label>
										))}
									</div>
								</fieldset>
								<div className="mt-5 flex flex-wrap gap-3">
									<button
										aria-label="APPROVED"
										className={primaryButtonClassName}
										disabled={busy}
										onClick={() => prepareDecision("APPROVED")}
										type="button"
									>
										<CheckCircle2
											aria-hidden="true"
											className="mr-2"
											size={17}
										/>
										批准入驻
									</button>
									<button
										aria-label="NEEDS_CORRECTION"
										className={secondaryButtonClassName}
										disabled={busy}
										onClick={() => prepareDecision("NEEDS_CORRECTION")}
										type="button"
									>
										要求补正
									</button>
									<button
										aria-label="REJECTED"
										className={`${secondaryButtonClassName} text-red-700 hover:border-red-300 hover:text-red-800`}
										disabled={busy}
										onClick={() => prepareDecision("REJECTED")}
										type="button"
									>
										拒绝申请
									</button>
								</div>
							</section>
						</div>
					)}
				</section>
			</div>

			{pendingDecision ? (
				<ConfirmDialog
					busy={busy}
					confirmLabel={decisionButtonLabel(pendingDecision)}
					description={decisionDescription(
						pendingDecision,
						selectedCorrectionFields.length,
					)}
					destructive={pendingDecision === "REJECTED"}
					onClose={() => setPendingDecision(null)}
					onConfirm={() => void confirmDecision()}
					title={decisionTitle(pendingDecision)}
				/>
			) : null}
		</main>
	);
}

function shortId(value: string) {
	return value.length > 12 ? `${value.slice(0, 8)}…` : value;
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
function licenseLabel(type: string) {
	return type === "MEDICAL_INSTITUTION_LICENSE"
		? "医疗机构执业许可证"
		: "营业执照";
}
function scanStatusLabel(status: string) {
	return (
		{
			CLEAN: "已通过",
			PENDING_SCAN: "扫描中",
			REJECTED: "未通过",
			SCAN_FAILED: "扫描失败",
		}[status] ?? status
	);
}
function decisionTitle(decision: Exclude<PendingDecision, null>) {
	return decision === "APPROVED"
		? "确认批准该机构入驻？"
		: decision === "NEEDS_CORRECTION"
			? "确认退回补正？"
			: "确认拒绝该申请？";
}
function decisionButtonLabel(decision: Exclude<PendingDecision, null>) {
	return decision === "APPROVED"
		? "确认批准"
		: decision === "NEEDS_CORRECTION"
			? "确认补正"
			: "确认拒绝";
}
function decisionDescription(
	decision: Exclude<PendingDecision, null>,
	count: number,
) {
	return decision === "APPROVED"
		? "批准后将创建并激活机构经营主体，但服务能力仍需完成后续准备。"
		: decision === "NEEDS_CORRECTION"
			? `机构将收到 ${count} 项补正要求，可修改后重新提交。`
			: "拒绝后本申请进入终态，机构不能在当前申请上继续补正。";
}
function decisionSuccessMessage(decision: Exclude<PendingDecision, null>) {
	return decision === "APPROVED"
		? "申请已批准，机构经营主体已激活，服务尚未就绪。"
		: decision === "NEEDS_CORRECTION"
			? "补正要求已发送，等待机构重新提交。"
			: "申请已拒绝。";
}
