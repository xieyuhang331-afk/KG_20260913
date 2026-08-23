import { useCallback, useEffect, useMemo, useState } from "react";
import { isApiError } from "@/shared/api/errors";
import {
	ConfirmDialog,
	EmptyPanel,
	Feedback,
	LoadingPanel,
	primaryButtonClassName,
	secondaryButtonClassName,
} from "@/domains/institution/受控入驻界面";
import {
	decideTherapistRenewalReview,
	decideTherapistReview,
	getTherapistReview,
	listTherapistRenewalReviews,
	listTherapistReviews,
} from "../api";
import type {
	TherapistReviewDecisionPayload,
	TherapistReviewDetail,
	TherapistReviewItem,
} from "../types";

type Mode = "INITIAL" | "RENEWAL";
type Decision = TherapistReviewDecisionPayload["decision"];
const copy: Record<Decision, [string, string]> = {
	START_REVIEW: ["领取审核", "确认领取"],
	NEEDS_CORRECTION: ["要求补正", "确认要求补正"],
	REJECTED: ["驳回资质", "确认驳回"],
	APPROVED: ["批准资质", "确认批准"],
};
function message(error: unknown, fallback: string) {
	if (!isApiError(error)) return fallback;
	if (error.status === 409)
		return "审核状态已变化，已刷新最新数据，请重新确认。";
	if (error.status === 503)
		return "服务暂不可用，结果暂时无法确认，请刷新后核对。";
	if (error.status === 404) return "审核对象不存在或已安全隐藏。";
	if (error.status === 403) return "当前账号没有审核权限。";
	return fallback;
}
function currentSet(detail: TherapistReviewDetail) {
	const ids = detail.current_qualification_ids;
	if (!ids.length || new Set(ids).size !== ids.length) return null;
	const groups = ids.map((id) =>
		detail.qualifications.filter((q) => q.qualification_version_id === id),
	);
	if (groups.some((group) => group.length !== 1)) return null;
	if (
		detail.review_item.review_kind === "RENEWAL" &&
		(ids.length !== 1 || detail.review_item.qualification_version_id !== ids[0])
	)
		return null;
	return groups.flat();
}
export function TherapistReviewPage() {
	const [mode, setMode] = useState<Mode>("INITIAL");
	const [items, setItems] = useState<TherapistReviewItem[]>([]);
	const [cursor, setCursor] = useState<string | null>();
	const [loading, setLoading] = useState(true);
	const [detail, setDetail] = useState<TherapistReviewDetail | null>(null);
	const [pending, setPending] = useState<Decision | null>(null);
	const [busy, setBusy] = useState(false);
	const [notice, setNotice] = useState("");
	const [error, setError] = useState("");
	const load = useCallback(async (kind: Mode, next?: string) => {
		setLoading(true);
		setError("");
		try {
			const value =
				kind === "INITIAL"
					? await listTherapistReviews({
							kind: "INITIAL",
							cursor: next,
							limit: 20,
						})
					: await listTherapistRenewalReviews({ cursor: next, limit: 20 });
			setItems(value.items);
			setCursor(value.next_cursor ?? null);
		} catch (reason) {
			setItems([]);
			setError(message(reason, "审核队列加载失败，请重试。"));
		} finally {
			setLoading(false);
		}
	}, []);
	useEffect(() => {
		setDetail(null);
		void load(mode);
	}, [load, mode]);
	const current = useMemo(() => (detail ? currentSet(detail) : null), [detail]);
	async function open(item: TherapistReviewItem) {
		setError("");
		try {
			setDetail(await getTherapistReview(item.therapist_id));
		} catch (reason) {
			setError(message(reason, "审核详情加载失败，请重试。"));
		}
	}
	async function decide() {
		if (!detail || !pending || !current) return;
		const decision = pending;
		const payload: TherapistReviewDecisionPayload = {
			decision,
			expected_version: detail.review_item.version,
			reason_code:
				decision === "APPROVED" || decision === "START_REVIEW"
					? null
					: "QUALIFICATION_REVIEW",
			profile_fields:
				decision === "NEEDS_CORRECTION" ? ["practice_summary"] : [],
			qualification_targets: [],
			qualification_outcomes:
				decision === "APPROVED" || decision === "REJECTED"
					? Object.fromEntries(
							current.map((q) => [
								q.qualification_version_id,
								decision === "APPROVED" ? "APPROVED" : "REJECTED",
							]),
						)
					: {},
		};
		setBusy(true);
		setError("");
		try {
			if (detail.review_item.review_kind === "RENEWAL")
				await decideTherapistRenewalReview(
					detail.review_item.review_item_id,
					payload,
					crypto.randomUUID(),
				);
			else
				await decideTherapistReview(
					detail.profile.therapist_id,
					payload,
					crypto.randomUUID(),
				);
			setPending(null);
			setNotice("审核决定已记录");
			await load(mode);
		} catch (reason) {
			setPending(null);
			if (isApiError(reason) && reason.status === 409) {
				setDetail(null);
				await load(mode);
			}
			setError(message(reason, "审核操作失败，请刷新后重试。"));
		} finally {
			setBusy(false);
		}
	}
	const history =
		detail?.qualifications.filter(
			(q) =>
				!new Set(detail.current_qualification_ids).has(
					q.qualification_version_id,
				),
		) ?? [];
	return (
		<main className="space-y-5 pb-10">
			<header>
				<p className="text-sm font-medium text-teal-700">
					平台治理 / 健管师资质
				</p>
				<h1 className="mt-1 text-2xl font-semibold">健管师资质审核</h1>
				<p className="mt-2 text-sm text-slate-600">
					审核决定始终基于当前资料版本和服务端状态。
				</p>
			</header>
			<Feedback message={error || notice} tone={error ? "error" : "success"} />
			<div className="flex gap-2">
				<button
					className={
						mode === "INITIAL"
							? primaryButtonClassName
							: secondaryButtonClassName
					}
					onClick={() => setMode("INITIAL")}
					type="button"
				>
					初始审核
				</button>
				<button
					className={
						mode === "RENEWAL"
							? primaryButtonClassName
							: secondaryButtonClassName
					}
					onClick={() => setMode("RENEWAL")}
					type="button"
				>
					续期审核
				</button>
			</div>
			<div className="grid items-start gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
				<div className="min-w-0">
					{loading ? (
						<LoadingPanel label="正在加载审核队列…" />
					) : !items.length ? (
						<EmptyPanel
							title={`暂无${mode === "INITIAL" ? "初始" : "续期"}审核`}
							description="当前没有待处理记录，可稍后刷新查看。"
						/>
					) : (
						<section className="overflow-hidden rounded-xl border bg-white shadow-panel">
							{items.map((item) => (
								<article
									className="flex items-center justify-between gap-3 border-b p-4 last:border-0"
									key={item.review_item_id}
								>
									<div className="min-w-0">
										<strong>
											{item.review_kind === "INITIAL"
												? "初始资质审核"
												: "续期资质审核"}
										</strong>
										<p className="text-sm text-slate-500">
											{reviewItemStatusLabel(item.status)} · 第 {item.version}{" "}
											版
										</p>
									</div>
									<button
										aria-label="查看审核详情"
										className={secondaryButtonClassName}
										onClick={() => void open(item)}
										type="button"
									>
										查看
									</button>
								</article>
							))}
							{cursor ? (
								<div className="p-4 text-right">
									<button
										className={secondaryButtonClassName}
										onClick={() => void load(mode, cursor)}
										type="button"
									>
										下一批审核
									</button>
								</div>
							) : null}
						</section>
					)}
				</div>
				{detail ? (
					<section className="min-w-0 rounded-xl border bg-white p-5 shadow-panel">
						<p className="text-sm text-slate-500">
							当前资料版本：第 {detail.profile.current_revision_no} 版
						</p>
						<h2 className="text-xl font-semibold">
							{detail.profile.display_name || "未设置展示名称"}
						</h2>
						{!current ? (
							<Feedback
								message="审核资质集合已变化，请刷新后重新打开详情。"
								tone="error"
							/>
						) : (
							<>
								<h3 className="mt-5 font-semibold">当前资格</h3>
								{current.map((q) => (
									<p className="mt-2 text-sm" key={q.qualification_version_id}>
										代谢健康执业资格第 {q.version_no} 版 ·{" "}
										{q.masked_certificate_no} · 材料 {q.attachment_count} 份
									</p>
								))}
								<h3 className="mt-5 font-semibold">历史版本</h3>
								{history.map((q) => (
									<p
										className="mt-2 text-sm text-slate-500"
										key={q.qualification_version_id}
									>
										第 {q.version_no} 版 ·{" "}
										{qualificationStatusLabel(q.derived_review_status)}
									</p>
								))}
								{detail.review_item.status !== "DECIDED" ? (
									<section
										aria-label="审核操作"
										className="sticky bottom-4 mt-6 flex justify-end gap-2 rounded-xl border bg-white/95 p-4 shadow-lg"
									>
										{detail.review_item.status === "QUEUED" ? (
											<button
												className={primaryButtonClassName}
												onClick={() => setPending("START_REVIEW")}
												type="button"
											>
												领取审核
											</button>
										) : (
											<>
												<button
													className={secondaryButtonClassName}
													onClick={() => setPending("NEEDS_CORRECTION")}
													type="button"
												>
													要求补正
												</button>
												<button
													className={secondaryButtonClassName}
													onClick={() => setPending("REJECTED")}
													type="button"
												>
													驳回
												</button>
												<button
													className={primaryButtonClassName}
													onClick={() => setPending("APPROVED")}
													type="button"
												>
													批准
												</button>
											</>
										)}
									</section>
								) : null}
							</>
						)}
					</section>
				) : (
					<section className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center shadow-panel">
						<p className="font-semibold text-slate-800">选择左侧审核任务</p>
						<p className="mt-2 text-sm text-slate-500">
							详情、当前资质和审核操作将在这里同步展开。
						</p>
					</section>
				)}
			</div>
			{pending ? (
				<ConfirmDialog
					busy={busy}
					confirmLabel={copy[pending][1]}
					description="该操作不会因冲突自动重试，请确认当前版本。"
					destructive={pending === "REJECTED"}
					onClose={() => setPending(null)}
					onConfirm={() => void decide()}
					title={copy[pending][0]}
				/>
			) : null}
		</main>
	);
}

function reviewItemStatusLabel(status: string) {
	return (
		{
			QUEUED: "待领取",
			UNDER_REVIEW: "审核中",
			DECIDED: "已完成",
		}[status] ?? "状态待确认"
	);
}

function qualificationStatusLabel(status: string) {
	return (
		{
			SUBMITTED: "待审核",
			APPROVED: "已批准",
			REJECTED: "已驳回",
			SUPERSEDED: "已被新版本替代",
		}[status] ?? "状态待确认"
	);
}
