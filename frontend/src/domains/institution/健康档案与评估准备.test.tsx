import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
	getAssessmentReadiness,
	getInstitutionHealthRecord,
	getInstitutionLatestHealthIndicators,
	listInstitutionDetectionReports,
} from "./api";
import { HealthRecordPage } from "./pages/HealthRecordPage";
import { MemberEnrollmentDetailPage } from "./pages/MemberEnrollmentDetailPage";
import { institutionRoutes } from "./routes";
import { toUuidV7 } from "@/shared/api/slice3";

describe("机构健康档案与评估准备", () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.restoreAllMocks();
	});

	it("冻结四个只读API路径且不发送请求体", async () => {
		const fetchMock = vi
			.fn()
			.mockImplementation(() =>
				Promise.resolve(success({ items: [], next_cursor: null })),
			);
		vi.stubGlobal("fetch", fetchMock);

		await getInstitutionHealthRecord(caseId);
		await listInstitutionDetectionReports(caseId, {
			cursor: "opaque-next",
			limit: 20,
		});
		await getInstitutionLatestHealthIndicators(caseId);
		await getAssessmentReadiness(caseId);

		expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
			`/api/v1/institution/service-cases/${caseId}/health-record`,
			`/api/v1/institution/service-cases/${caseId}/detection-reports?cursor=opaque-next&limit=20`,
			`/api/v1/institution/service-cases/${caseId}/health-indicators/latest`,
			`/api/v1/service-cases/${caseId}/assessment-readiness`,
		]);
		for (const [, options] of fetchMock.mock.calls as Array<
			[string, RequestInit | undefined]
		>) {
			expect(options?.method ?? "GET").toBe("GET");
			expect(options?.body).toBeUndefined();
		}
	});

	it("注册健康档案路由", () => {
		expect(institutionRoutes.protectedChildren).toEqual(
			expect.arrayContaining([
				expect.objectContaining({
					path: "service-cases/:caseId/health-record",
				}),
			]),
		);
	});

	it("从已创建服务案例的入组详情进入健康档案", async () => {
		mockByPath({
			[`/api/v1/institution/member-enrollments/${enrollmentId}`]:
				enrollmentDetail(),
			"/api/v1/institution/therapists?status=APPROVED_ACTIVE&limit=100": {
				items: [],
				next_cursor: null,
			},
			[`/api/v1/institution/service-cases/${caseId}`]: preparingCase(),
		});

		render(
			<MemoryRouter
				initialEntries={[`/institution/member-enrollments/${enrollmentId}`]}
			>
				<Routes>
					<Route
						element={<MemberEnrollmentDetailPage />}
						path="/institution/member-enrollments/:enrollmentId"
					/>
				</Routes>
			</MemoryRouter>,
		);

		expect(
			await screen.findByRole("link", { name: "查看客户健康档案与评估准备" }),
		).toHaveAttribute(
			"href",
			`/institution/service-cases/${caseId}/health-record`,
		);
	});

	it("以正向状态展示最小档案摘要、服务端指标和报告元数据", async () => {
		mockHealthPage({ readiness: readiness("ASSESSMENT_READY") });
		renderHealthPage();

		expect(
			await screen.findByRole("heading", { name: "客户健康档案与评估准备" }),
		).toBeInTheDocument();
		expect(screen.getByText("当前服务案例")).toBeInTheDocument();
		expect(screen.getAllByText("档案已完整")).not.toHaveLength(0);
		expect(screen.getAllByText("已具备评估条件")).not.toHaveLength(0);
		expect(screen.getByText("26.3")).toBeInTheDocument();
		expect(screen.getByText("服务端已计算")).toBeInTheDocument();
		expect(screen.getByText("年度体检报告")).toBeInTheDocument();
		expect(screen.getByText("2 个附件")).toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: /原件|下载|查看附件/ }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByText(/病史详情|过敏详情|用药详情|症状详情/),
		).not.toBeInTheDocument();
		expect(
			screen.queryByText(/^(评估结果|风险结论|健康方案)$/),
		).not.toBeInTheDocument();
		expect(
			screen.getByText("输入条件齐备不等于已产生评估结果"),
		).toBeInTheDocument();
	});

	it("档案不完整时展示缺失模块而不展开健康详情", async () => {
		mockHealthPage({
			record: {
				...healthRecord(),
				profile_completion_status: "INCOMPLETE",
				missing_section_codes: ["HEALTH_PROFILE"],
			},
		});
		renderHealthPage();

		expect(await screen.findByText("基础健康档案待补充")).toBeInTheDocument();
		expect(
			screen.queryByText(/病史详情|过敏详情|用药详情|症状详情/),
		).not.toBeInTheDocument();
	});

	it.each([
		["DATA_INSUFFICIENT", "资料尚不充足", "体重"],
		["DATA_SYNC_PENDING", "数据同步中", "系统正在汇总最新数据"],
		["DISPUTED", "存在待确认指标", "糖化血红蛋白"],
		["ASSESSMENT_READY", "已具备评估条件", "仅表示评估所需输入条件齐备"],
	] as const)("将%s显示为安全业务状态", async (status, heading, detail) => {
		mockHealthPage({ readiness: readiness(status) });
		renderHealthPage();

		expect(await screen.findAllByText(heading)).not.toHaveLength(0);
		expect(screen.getAllByText(new RegExp(detail))).not.toHaveLength(0);
		expect(screen.queryByText(status)).not.toBeInTheDocument();
	});

	it("503仅显示安全恢复提示并支持重试", async () => {
		let recordAttempts = 0;
		const fetchMock = vi.fn((input: RequestInfo | URL) => {
			const url = String(input);
			if (url.endsWith("/health-record")) {
				recordAttempts += 1;
				return Promise.resolve(
					recordAttempts === 1
						? failure(503, "DEPENDENCY_UNAVAILABLE", "database secret")
						: success(healthRecord()),
				);
			}
			if (url.includes("/detection-reports"))
				return Promise.resolve(success(reportPage()));
			if (url.endsWith("/health-indicators/latest"))
				return Promise.resolve(success(latestIndicators()));
			if (url.endsWith("/assessment-readiness"))
				return Promise.resolve(success(readiness("ASSESSMENT_READY")));
			throw new Error(`UNEXPECTED_TEST_PATH:${url}`);
		});
		vi.stubGlobal("fetch", fetchMock);
		renderHealthPage();

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"服务暂时不可用，请稍后重试",
		);
		expect(screen.queryByText("database secret")).not.toBeInTheDocument();
		await userEvent.click(screen.getByRole("button", { name: "重新加载" }));
		expect(await screen.findAllByText("已具备评估条件")).not.toHaveLength(0);
	});

	it("报告列表使用opaque cursor翻批且不虚构总页数", async () => {
		const first = reportPage({ next_cursor: "opaque-next" });
		const next = reportPage({ items: [], next_cursor: null });
		const fetchMock = vi.fn((input: RequestInfo | URL) => {
			const url = String(input);
			if (url.endsWith("/health-record"))
				return Promise.resolve(success(healthRecord()));
			if (url.includes("/detection-reports"))
				return Promise.resolve(
					success(url.includes("cursor=opaque-next") ? next : first),
				);
			if (url.endsWith("/health-indicators/latest"))
				return Promise.resolve(success(latestIndicators()));
			if (url.endsWith("/assessment-readiness"))
				return Promise.resolve(success(readiness("ASSESSMENT_READY")));
			throw new Error(`UNEXPECTED_TEST_PATH:${url}`);
		});
		vi.stubGlobal("fetch", fetchMock);
		renderHealthPage();

		const nextButton = await screen.findByRole("button", { name: "下一批" });
		await userEvent.click(nextButton);
		await waitFor(() =>
			expect(
				fetchMock.mock.calls.some(([url]) =>
					String(url).includes("cursor=opaque-next"),
				),
			).toBe(true),
		);
		expect(screen.queryByText(/共\s*\d+\s*页/)).not.toBeInTheDocument();
	});
});

const caseId = toUuidV7("0198c4a1-1111-7abc-8000-000000000101");
const enrollmentId = "0198c4a1-1111-7abc-8000-000000000102";

function renderHealthPage() {
	return render(
		<MemoryRouter
			initialEntries={[`/institution/service-cases/${caseId}/health-record`]}
		>
			<Routes>
				<Route
					element={<HealthRecordPage />}
					path="/institution/service-cases/:caseId/health-record"
				/>
			</Routes>
		</MemoryRouter>,
	);
}

function mockHealthPage(
	overrides: { record?: object; readiness?: object; reports?: object } = {},
) {
	const fetchMock = vi.fn((input: RequestInfo | URL) => {
		const url = String(input);
		if (url.endsWith("/health-record"))
			return Promise.resolve(success(overrides.record ?? healthRecord()));
		if (url.includes("/detection-reports"))
			return Promise.resolve(success(overrides.reports ?? reportPage()));
		if (url.endsWith("/health-indicators/latest"))
			return Promise.resolve(success(latestIndicators()));
		if (url.endsWith("/assessment-readiness"))
			return Promise.resolve(
				success(overrides.readiness ?? readiness("ASSESSMENT_READY")),
			);
		throw new Error(`UNEXPECTED_TEST_PATH:${url}`);
	});
	vi.stubGlobal("fetch", fetchMock);
	return fetchMock;
}

function mockByPath(values: Record<string, object>) {
	const fetchMock = vi.fn((input: RequestInfo | URL) => {
		const path =
			new URL(String(input), "http://local.test").pathname +
			new URL(String(input), "http://local.test").search;
		const value = values[path];
		if (!value) throw new Error(`UNEXPECTED_TEST_PATH:${path}`);
		return Promise.resolve(success(value));
	});
	vi.stubGlobal("fetch", fetchMock);
	return fetchMock;
}

function healthRecord() {
	return {
		case_id: caseId,
		profile_completion_status: "COMPLETE",
		missing_section_codes: [],
		indicator_codes: ["height", "weight", "bmi", "hba1c"],
		indicator_states: {
			height: "VERIFIED",
			weight: "VERIFIED",
			bmi: "VERIFIED",
			hba1c: "DISPUTED",
		},
		report_metadata_count: 1,
		readiness_status: "ASSESSMENT_READY",
		updated_at: "2026-08-23T09:20:00+08:00",
	};
}

function latestIndicators() {
	return {
		items: [
			fact("height", "160", "cm", "VERIFIED", 1),
			fact("weight", "80", "kg", "VERIFIED", 2),
			fact("bmi", "26.3", "kg/m2", "VERIFIED", 3),
			fact("hba1c", "6.1", "%", "DISPUTED", 4),
		],
	};
}

function fact(
	code: string,
	value: string,
	unit: string,
	state = "VERIFIED",
	index = 1,
) {
	return {
		fact_ref: `0198c4a1-1111-7abc-8000-00000000020${index}`,
		indicator_code: code,
		value,
		unit,
		measured_at: "2026-08-22T08:00:00+08:00",
		received_at: "2026-08-22T08:01:00+08:00",
		source: "STORE",
		verification_state: state,
	};
}

function reportPage(overrides: object = {}) {
	return {
		items: [
			{
				report_id: "0198c4a1-1111-7abc-8000-000000000301",
				subject_ref: "0198c4a1-1111-7abc-8000-000000000302",
				report_type: "PHYSICAL_EXAM",
				measured_at: "2026-08-20T09:00:00+08:00",
				received_at: "2026-08-20T09:10:00+08:00",
				status: "READY",
				source: "APP",
				attachment_count: 2,
				structured_indicator_codes: ["hba1c"],
				supersedes_report_id: null,
				version: 1,
				created_at: "2026-08-20T09:10:00+08:00",
			},
		],
		next_cursor: null,
		...overrides,
	};
}

function readiness(
	status:
		| "DATA_INSUFFICIENT"
		| "DATA_SYNC_PENDING"
		| "DISPUTED"
		| "ASSESSMENT_READY",
) {
	return {
		service_case_id: caseId,
		status,
		reason_codes:
			status === "DATA_INSUFFICIENT"
				? ["INDICATOR_MISSING"]
				: status === "DATA_SYNC_PENDING"
					? ["RECOMPUTE_PENDING"]
					: status === "DISPUTED"
						? ["INDICATOR_DISPUTED"]
						: [],
		missing_indicator_codes: status === "DATA_INSUFFICIENT" ? ["weight"] : [],
		expired_indicator_codes: [],
		disputed_indicator_codes: status === "DISPUTED" ? ["hba1c"] : [],
		profile_revision_id: "0198c4a1-1111-7abc-8000-000000000401",
		policy_version: "ASSESSMENT_INPUT_V1",
		projection_status:
			status === "DATA_SYNC_PENDING" ? "SYNC_PENDING" : "CURRENT",
		data_as_of: "2026-08-23T09:00:00+08:00",
		generated_at: "2026-08-23T09:20:00+08:00",
		assembly_id:
			status === "ASSESSMENT_READY"
				? "0198c4a1-1111-7abc-8000-000000000402"
				: null,
	};
}

function enrollmentDetail() {
	return {
		enrollment_id: enrollmentId,
		tenant_id: "0198c4a1-1111-7abc-8000-000000000501",
		subject_member_id: "0198c4a1-1111-7abc-8000-000000000502",
		proxy_member_id: null,
		mode: "SELF",
		status: "CASE_CREATED",
		service_scope_tags: ["OBESITY"],
		current_identity_verification_id: null,
		current_assignment_id: null,
		service_case_id: caseId,
		accepted_at: "2026-08-21T08:00:00+08:00",
		identity_verified_at: "2026-08-22T08:00:00+08:00",
		case_created_at: "2026-08-23T08:00:00+08:00",
		version: 5,
		identity: null,
		proxy: null,
		consents: [],
		assignment: null,
	};
}

function preparingCase() {
	return {
		case_id: caseId,
		enrollment_id: enrollmentId,
		subject_member_id: "0198c4a1-1111-7abc-8000-000000000502",
		tenant_id: "0198c4a1-1111-7abc-8000-000000000501",
		primary_therapist_id: "0198c4a1-1111-7abc-8000-000000000503",
		assignment_id: "0198c4a1-1111-7abc-8000-000000000504",
		status: "PREPARING",
		service_scope_tags: ["OBESITY"],
		created_at: "2026-08-23T08:00:00+08:00",
		version: 1,
	};
}

function success(data: unknown) {
	return new Response(JSON.stringify({ data, request_id: caseId }), {
		status: 200,
		headers: { "Content-Type": "application/json" },
	});
}

function failure(status: number, code: string, message: string) {
	return new Response(JSON.stringify({ code, message }), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}
