import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemberInvitationPage } from "./pages/MemberInvitationPage";
import { MemberEnrollmentPage } from "./pages/MemberEnrollmentPage";
import { MemberEnrollmentDetailPage } from "./pages/MemberEnrollmentDetailPage";
import { institutionNavigation } from "./navigation";
import { InstitutionShell } from "@/shells/InstitutionShell";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";

describe("机构客户服务邀约与服务接入", () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.restoreAllMocks();
		setCurrentUser(null);
	});

	it("正式导航使用客户服务术语并保留原有路由合同", () => {
		expect(institutionNavigation).toEqual(
			expect.arrayContaining([
				{ label: "客户服务邀约", path: "/institution/member-invitations" },
				{ label: "服务客户列表", path: "/institution/member-enrollments" },
			]),
		);
		expect(institutionNavigation.map((item) => item.label)).not.toEqual(
			expect.arrayContaining(["会员邀请", "会员入组", "健管师邀请", "试点健管师接入"]),
		);
	});

	it("客户服务邀约不包装为会员开通并保留一次性短码说明", () => {
		render(<MemberInvitationPage />, { wrapper: MemoryRouter });
		expect(screen.getByRole("heading", { name: "客户服务邀约" })).toBeInTheDocument();
		expect(screen.getByText(/短码只会展示一次/)).toBeInTheDocument();
		expect(screen.queryByRole("button", { name: /会员开通|会员购买/ })).not.toBeInTheDocument();
	});

	it("服务客户列表不包装为会员购买并说明 cursor 批次而非总页数", () => {
		render(<MemberEnrollmentPage />, { wrapper: MemoryRouter });
		expect(screen.getByRole("heading", { name: "服务客户列表" })).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "上一批" })).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "下一批" })).toBeInTheDocument();
		expect(screen.queryByText(/共\s*\d+\s*页/)).not.toBeInTheDocument();
		expect(screen.queryByRole("button", { name: /会员开通|会员购买/ })).not.toBeInTheDocument();
	});

	it("机构Shell不展示内部数值型门店ID", () => {
		setCurrentUser({
			id: 3,
			role: USER_ROLES.orgAdmin,
			tenant_id: 501,
			org_id: 31,
		});
		render(
			<MemoryRouter initialEntries={["/institution/member-invitations"]}>
				<Routes>
					<Route element={<InstitutionShell />} path="/institution">
						<Route
							element={<div>会员邀请内容</div>}
							path="member-invitations"
						/>
					</Route>
				</Routes>
			</MemoryRouter>,
		);
		expect(screen.getByText("当前机构")).toBeInTheDocument();
		expect(screen.queryByText(/501/)).not.toBeInTheDocument();
	});

	it("创建成功后短码仅在对话框内一次性展示并可用 Escape 清除", async () => {
		const shortCode = ["246", "810"].join("");
		mockSequence(
			success(page([])),
			success(invitation({ short_code: shortCode })),
			success(page([])),
		);
		render(<MemberInvitationPage />, { wrapper: MemoryRouter });
		await screen.findByText("当前批次没有邀请");

		await userEvent.type(screen.getByLabelText("接收手机号"), "18800000000");
		await userEvent.click(screen.getByRole("button", { name: "创建邀请" }));

		const dialog = await screen.findByRole("dialog", {
			name: "一次性邀请短码",
		});
		expect(screen.getByText(shortCode)).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "我已安全记录" })).toHaveFocus();
		await userEvent.keyboard("{Escape}");
		expect(dialog).not.toBeInTheDocument();
		expect(screen.queryByText(shortCode)).not.toBeInTheDocument();
		await waitFor(() =>
			expect(screen.getByLabelText("接收手机号")).toHaveFocus(),
		);
	});

	it("撤销发生 409 时只刷新且不自动重放 mutation", async () => {
		vi.stubGlobal(
			"confirm",
			vi.fn(() => true),
		);
		const fetchMock = mockSequence(
			success(page([invitation()])),
			failure(409, "VERSION_CONFLICT", "private detail"),
			success(page([invitation({ version: 2 })])),
		);
		render(<MemberInvitationPage />, { wrapper: MemoryRouter });
		await screen.findByText("188****0000");
		await userEvent.click(screen.getByRole("button", { name: "撤销" }));
		await screen.findByText("数据已更新，请刷新后重试");
		expect(screen.queryByText("private detail")).not.toBeInTheDocument();
		expect(
			fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/revoke")),
		).toHaveLength(1);
		expect(fetchMock).toHaveBeenCalledTimes(3);
	});

	it("线下实名核验提交当前 revision、expected_version 与幂等键", async () => {
		const fetchMock = mockSequence(
			success(enrollmentDetail()),
			success(page([])),
			success({ status: "CHECKED" }),
			success(
				enrollmentDetail({ identity_verified_at: "2026-08-21T08:00:00+08:00" }),
			),
			success(page([])),
		);
		render(
			<MemoryRouter
				initialEntries={[`/institution/member-enrollments/${invitationId}`]}
			>
				<Routes>
					<Route
						element={<MemberEnrollmentDetailPage />}
						path="/institution/member-enrollments/:enrollmentId"
					/>
				</Routes>
			</MemoryRouter>,
		);
		await screen.findByText("110***********1234");
		await userEvent.click(
			screen.getByRole("button", { name: "确认并提交核验" }),
		);
		await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));

		const [url, options] = fetchMock.mock.calls[2] as [string, RequestInit];
		expect(url).toContain(
			`/api/v1/institution/member-enrollments/${invitationId}/identity-check`,
		);
		expect(new Headers(options.headers).get("Idempotency-Key")).toBeTruthy();
		expect(JSON.parse(String(options.body))).toEqual({
			revision_id: "0198b963-38f0-7d7d-8000-000000000033",
			decision: "CHECKED",
			reason_code: null,
			correction_fields: [],
			attestation_code: "OFFLINE_IDENTITY_CHECKED",
			expected_version: 4,
		});
	});

	it("客户服务接入详情使用业务文案且不把服务客户显示为付费会员", async () => {
		mockSequence(
			success(
				enrollmentDetail({
					status: "INSTITUTION_CHECKED",
					identity: {
						...enrollmentDetail().identity,
						status: "INSTITUTION_CHECKED",
					},
				}),
			),
			success(page([])),
		);
		render(
			<MemoryRouter
				initialEntries={[`/institution/member-enrollments/${invitationId}`]}
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
			(await screen.findAllByText("机构核验通过，待平台终审")).length,
		).toBeGreaterThanOrEqual(1);
		expect(screen.getByText("实名材料版本")).toBeInTheDocument();
		expect(screen.queryByText("INSTITUTION_CHECKED")).not.toBeInTheDocument();
		expect(screen.queryByText("Revision")).not.toBeInTheDocument();
		expect(screen.queryByText(/0198b963…000031/)).not.toBeInTheDocument();
		expect(screen.queryByText(/0198b963…000033/)).not.toBeInTheDocument();
		expect(screen.getByRole("heading", { name: "客户服务接入详情" })).toBeInTheDocument();
		expect(screen.queryByText(/付费会员|会员购买/)).not.toBeInTheDocument();
	});
});

const invitationId = "0198b963-38f0-7d7d-8000-000000000031";

function invitation(overrides: object = {}) {
	return {
		invitation_id: invitationId,
		tenant_id: "0198b963-38f0-7d7d-8000-000000000032",
		mode: "SELF",
		phone_masked: "188****0000",
		expires_at: "2026-08-22T00:00:00+08:00",
		status: "INVITED",
		failed_attempts: 0,
		issued_at: "2026-08-21T00:00:00+08:00",
		accepted_at: null,
		revoked_at: null,
		version: 1,
		...overrides,
	};
}

function enrollmentDetail(overrides: object = {}) {
	return {
		enrollment_id: invitationId,
		tenant_id: "0198b963-38f0-7d7d-8000-000000000032",
		subject_member_id: "0198b963-38f0-7d7d-8000-000000000034",
		proxy_member_id: null,
		mode: "SELF",
		status: "IDENTITY_SUBMITTED",
		service_scope_tags: [],
		current_identity_verification_id: "0198b963-38f0-7d7d-8000-000000000035",
		current_assignment_id: null,
		service_case_id: null,
		accepted_at: "2026-08-21T00:00:00+08:00",
		identity_verified_at: null,
		case_created_at: null,
		version: 5,
		identity: {
			verification_id: "0198b963-38f0-7d7d-8000-000000000035",
			enrollment_id: invitationId,
			member_id: "0198b963-38f0-7d7d-8000-000000000034",
			current_revision_id: "0198b963-38f0-7d7d-8000-000000000033",
			status: "SUBMITTED",
			id_masked: "110***********1234",
			submitted_at: "2026-08-21T00:00:00+08:00",
			institution_checked_at: null,
			platform_decided_at: null,
			reason_codes: [],
			version: 4,
		},
		proxy: null,
		consents: [],
		assignment: null,
		...overrides,
	};
}

function mockSequence(...responses: Response[]) {
	const fetchMock = vi.fn();
	for (const response of responses) fetchMock.mockResolvedValueOnce(response);
	vi.stubGlobal("fetch", fetchMock);
	return fetchMock;
}

function success(data: unknown) {
	return new Response(JSON.stringify({ data, request_id: invitationId }), {
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

function page(items: object[]) {
	return { items, next_cursor: null };
}
