import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { institutionRoutes } from "@/domains/institution/routes";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { listIdentityReviews } from "./实名认证审核接口";
import { IdentityReviewDetailPage } from "./pages/实名认证审核详情页";
import { IdentityReviewListPage } from "./pages/实名认证审核列表页";
import { platformRoutes } from "./routes";

describe("Platform Identity Review Workbench", () => {
  beforeEach(() => {
    setCurrentUser({ id: 1, role: USER_ROLES.superAdmin, tenant_id: null, org_id: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requests only the submitted queue and maps only masked identity fields", async () => {
    const fetchMock = mockApi(200, {
      items: [queueItem({ unexpected_sensitive_field: "forbidden-full-value" })],
      page: 2,
      page_size: 20,
      total: 41,
    });

    const result = await listIdentityReviews({ page: 2, page_size: 20 });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/reviews/identity?status=submitted&page=2&page_size=20",
      expect.any(Object),
    );
    expect(result.items[0]).toEqual({
      user_id: 21,
      submission_version: 3,
      id_card_masked: "110***********1234",
      submitted_at: "2026-08-10T01:02:03Z",
    });
    expect(result.items[0]).not.toHaveProperty("unexpected_sensitive_field");
  });

  it("renders queue data without rendering unexpected full-value fields", async () => {
    mockApi(200, {
      items: [queueItem({ unexpected_sensitive_field: "forbidden-full-value" })],
      page: 1,
      page_size: 20,
      total: 1,
    });

    renderList();

    expect(await screen.findByText("110***********1234")).toBeInTheDocument();
    expect(screen.queryByText("forbidden-full-value")).not.toBeInTheDocument();
    expect(screen.getByText("待审核")).toBeInTheDocument();
  });

  it("renders an empty state", async () => {
    mockApi(200, emptyPage());
    renderList();
    expect(await screen.findByText("暂无待审核实名认证")).toBeInTheDocument();
  });

  it("handles 503 safely and retries on demand", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(apiResponse(503, { detail: "internal-sensitive-detail" }))
      .mockResolvedValueOnce(apiSuccess(emptyPage()));
    vi.stubGlobal("fetch", fetchMock);

    renderList();
    expect(await screen.findByText("实名认证审核服务暂不可用，请稍后重试。")).toBeInTheDocument();
    expect(screen.queryByText("internal-sensitive-detail")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("暂无待审核实名认证")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    [403, "当前账号没有实名认证审核权限。"],
    [422, "审核队列请求参数无效，请刷新页面后重试。"],
  ])("maps queue %s to a safe error", async (status, message) => {
    mockApi(status, { detail: "internal-sensitive-detail" });
    renderList();
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText("internal-sensitive-detail")).not.toBeInTheDocument();
  });

  it("clears the frontend session and redirects to platform login on queue 401", async () => {
    mockApi(401, { detail: "Unauthorized" });
    renderList();
    expect(await screen.findByText("LOGIN TARGET")).toBeInTheDocument();
  });

  it.each([USER_ROLES.provinceAdmin, USER_ROLES.cityAdmin, USER_ROLES.orgAdmin])(
    "rejects non-super-admin role %s at the identity route boundary",
    async (role) => {
      setCurrentUser({ id: 2, role, tenant_id: role === USER_ROLES.orgAdmin ? 9 : null, org_id: null });
      const router = createMemoryRouter(
        [
          { path: "/platform", children: platformRoutes.protectedChildren },
          { path: "/403", element: <div>FORBIDDEN TARGET</div> },
        ],
        { initialEntries: ["/platform/identity-reviews"] },
      );

      render(<RouterProvider router={router} />);
      expect(await screen.findByText("FORBIDDEN TARGET")).toBeInTheDocument();
      expect(screen.queryByText("实名认证审核工作台")).not.toBeInTheDocument();
    },
  );

  it("keeps sensitive detail hidden and makes no request before step-up", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    renderDetail();

    expect(screen.getByText("敏感身份信息默认隐藏")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "审核驳回" })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("submits only the current password, consumes the step-up header, and reveals detail temporarily", async () => {
    const fetchMock = mockStepUpAndDetail();
    renderDetail();

    await completeStepUp();

    expect(screen.getByText("FULL_ID_VALUE")).toBeInTheDocument();
    expect(screen.getByText("Review Subject")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);

    const [stepUpUrl, stepUpOptions] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(stepUpUrl).toBe("/api/v1/reviews/users/21/identity/step-up");
    expect(JSON.parse(String(stepUpOptions.body))).toEqual({ password: "CURRENT_PASSWORD_INPUT" });
    expect(stepUpUrl).not.toContain("CURRENT_PASSWORD_INPUT");

    const [detailUrl, detailOptions] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(detailUrl).toBe("/api/v1/reviews/users/21/identity?purpose_code=MANUAL_REVIEW");
    expect(new Headers(detailOptions.headers).get("X-Identity-Review-Step-Up")).toBe("STEP_UP_VALUE");
    expect(detailUrl).not.toContain("STEP_UP_VALUE");
    expect(detailOptions.body).toBeUndefined();
  });

  it("keeps password, step-up token, and temporary identity out of React Query caches", async () => {
    mockStepUpAndDetail();
    const { queryClient } = renderDetail();

    await completeStepUp();

    const cachedState = JSON.stringify({
      queries: queryClient
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
      mutations: queryClient
        .getMutationCache()
        .getAll()
        .map((mutation) => mutation.state.variables),
    });
    expect(cachedState).not.toContain("CURRENT_PASSWORD_INPUT");
    expect(cachedState).not.toContain("STEP_UP_VALUE");
    expect(cachedState).not.toContain("FULL_ID_VALUE");
  });

  it("automatically hides the temporary identity when the 120-second window expires", async () => {
    mockStepUpAndDetail();
    const timeoutSpy = vi.spyOn(window, "setTimeout");
    renderDetail();

    await completeStepUp();
    expect(screen.getByText("FULL_ID_VALUE")).toBeInTheDocument();

    const expiryCall = timeoutSpy.mock.calls.find(([, delay]) => Number(delay) >= 119_000);
    expect(expiryCall).toBeDefined();
    const expiryHandler = expiryCall?.[0];
    act(() => {
      if (typeof expiryHandler === "function") expiryHandler();
    });

    expect(screen.queryByText("FULL_ID_VALUE")).not.toBeInTheDocument();
    expect(screen.getByText("临时查看已到期，敏感身份信息已自动隐藏。")).toBeInTheDocument();
  });

  it("immediately hides temporary identity on reviewer demand", async () => {
    mockStepUpAndDetail();
    renderDetail();
    await completeStepUp();

    await userEvent.click(screen.getByRole("button", { name: "立即隐藏" }));
    expect(screen.queryByText("FULL_ID_VALUE")).not.toBeInTheDocument();
    expect(screen.getByText("敏感身份信息已立即隐藏。")).toBeInTheDocument();
  });

  it("removes the temporary identity from the page when the reviewer leaves", async () => {
    mockStepUpAndDetail();
    renderDetail();
    await completeStepUp();

    await userEvent.click(screen.getByRole("link", { name: "返回待审核队列" }));
    expect(await screen.findByText("QUEUE TARGET")).toBeInTheDocument();
    expect(screen.queryByText("FULL_ID_VALUE")).not.toBeInTheDocument();
  });

  it.each([
    [401, "密码错误、验证已过期或登录状态无效，请重新确认。"],
    [403, "当前账号没有执行实名认证审核的权限。"],
    [404, "待审核实名认证不存在或状态已变化。"],
    [422, "实名认证审核请求不符合当前合同，请刷新页面后重试。"],
    [429, "二次认证尝试过于频繁，请稍后重试。"],
    [503, "实名认证审核服务暂不可用，请稍后重试。"],
  ])("maps step-up %s without exposing backend detail", async (status, message) => {
    const fetchMock = vi.fn().mockResolvedValue(apiResponse(status, { detail: "internal-sensitive-detail" }));
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();

    await submitPassword();

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText("internal-sensitive-detail")).not.toBeInTheDocument();
    expect(screen.queryByText("FULL_ID_VALUE")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it.each([401, 403])("rejects expired, replayed, or cross-user step-up on detail %s", async (status) => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(apiSuccess(stepUpData()))
      .mockResolvedValueOnce(apiResponse(status, { detail: "internal-sensitive-detail" }));
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();

    await submitPassword();

    expect(
      await screen.findByText(status === 401 ? /密码错误、验证已过期/ : /没有执行实名认证审核的权限/),
    ).toBeInTheDocument();
    expect(screen.queryByText("FULL_ID_VALUE")).not.toBeInTheDocument();
  });

  it("rejects an unexpected step-up response without attempting detail", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(apiSuccess({ step_up_token: "STEP_UP_VALUE", token_type: "unexpected", expires_in: 120 }));
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();

    await submitPassword();

    expect(await screen.findByText("实名认证审核请求失败，请稍后重试。")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("sends the exact approve contract and omits client decision time and evidence", async () => {
    const fetchMock = mockStepUpDetailAndDecision(200, {
      user_id: 21,
      submission_version: 3,
      status: "verified",
      decision_ref: "decision-ref",
      replayed: false,
    });
    renderDetail();
    await completeStepUp();

    await userEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await userEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByText("QUEUE TARGET")).toBeInTheDocument();

    const [url, options] = fetchMock.mock.calls[2] as [string, RequestInit];
    const payload = JSON.parse(String(options.body));
    expect(url).toBe("/api/v1/reviews/users/21/identity/approve");
    expect(payload).toEqual({
      idempotency_key: expect.stringMatching(/^identity-review-/),
      submission_version: 3,
      decision_basis_code: "APPROVED_OFFLINE_IDENTITY_CHECK",
    });
    expect(payload).not.toHaveProperty("decided_at");
    expect(payload).not.toHaveProperty("evidence_digest");
  });

  it("sends the exact reject contract without client-generated decision fields", async () => {
    const fetchMock = mockStepUpDetailAndDecision(200, {
      user_id: 21,
      submission_version: 3,
      status: "rejected",
      replayed: false,
    });
    renderDetail();
    await completeStepUp();

    await userEvent.click(screen.getByRole("button", { name: "审核驳回" }));
    await userEvent.click(screen.getByRole("button", { name: "确认驳回" }));
    expect(await screen.findByText("QUEUE TARGET")).toBeInTheDocument();

    const [url, options] = fetchMock.mock.calls[2] as [string, RequestInit];
    const payload = JSON.parse(String(options.body));
    expect(url).toBe("/api/v1/reviews/users/21/identity/reject");
    expect(payload).toEqual({
      idempotency_key: expect.stringMatching(/^identity-review-/),
      submission_version: 3,
      reason_code: "OFFLINE_CHECK_FAILED",
    });
    expect(payload).not.toHaveProperty("decided_at");
    expect(payload).not.toHaveProperty("evidence_digest");
  });

  it("clears sensitive state and requires fresh verification after 409", async () => {
    mockStepUpDetailAndDecision(409, { detail: "internal-sensitive-detail" });
    renderDetail();
    await completeStepUp();

    await userEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await userEvent.click(screen.getByRole("button", { name: "确认通过" }));

    expect(await screen.findByText("审核状态已变化，需重新验证后读取最新详情。")).toBeInTheDocument();
    expect(screen.queryByText("FULL_ID_VALUE")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "验证并临时查看" })).toBeInTheDocument();
    expect(screen.queryByText("internal-sensitive-detail")).not.toBeInTheDocument();
  });

  it("clears the session and sensitive state on decision 401", async () => {
    mockStepUpDetailAndDecision(401, { detail: "internal-sensitive-detail" });
    renderDetail();
    await completeStepUp();

    await userEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await userEvent.click(screen.getByRole("button", { name: "确认通过" }));

    expect(await screen.findByText("LOGIN TARGET")).toBeInTheDocument();
    expect(screen.queryByText("FULL_ID_VALUE")).not.toBeInTheDocument();
    expect(screen.queryByText("internal-sensitive-detail")).not.toBeInTheDocument();
  });

  it("prevents duplicate decisions and reuses the idempotency key for a safe 503 retry", async () => {
    let resolveFirstDecision: ((response: Response) => void) | undefined;
    const pendingDecision = new Promise<Response>((resolve) => {
      resolveFirstDecision = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(apiSuccess(stepUpData()))
      .mockResolvedValueOnce(apiSuccess(detailData()))
      .mockReturnValueOnce(pendingDecision)
      .mockResolvedValueOnce(apiResponse(503, { detail: "internal-sensitive-detail" }))
      .mockResolvedValueOnce(
        apiSuccess({ user_id: 21, submission_version: 3, status: "verified", decision_ref: "ref", replayed: true }),
      );
    vi.stubGlobal("fetch", fetchMock);
    renderDetail();
    await completeStepUp();

    await userEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await userEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(screen.getByRole("button", { name: "正在提交..." })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(3);

    resolveFirstDecision?.(apiResponse(503, { detail: "internal-sensitive-detail" }));
    expect(await screen.findByText("实名认证审核服务暂不可用，请稍后重试。")).toBeInTheDocument();
    const firstPayload = JSON.parse(String((fetchMock.mock.calls[2][1] as RequestInit).body));

    await userEvent.click(screen.getByRole("button", { name: "确认通过" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    const retryPayload = JSON.parse(String((fetchMock.mock.calls[3][1] as RequestInit).body));
    expect(retryPayload.idempotency_key).toBe(firstPayload.idempotency_key);
  });

  it("keeps P1 platform and institution routes renderable", async () => {
    const fetchMock = vi.fn().mockResolvedValue(apiSuccess(emptyPage()));
    vi.stubGlobal("fetch", fetchMock);
    const platformRouter = createMemoryRouter([{ path: "/platform", children: platformRoutes.protectedChildren }], {
      initialEntries: ["/platform/stores/reviews"],
    });
    const platformRender = renderWithClient(<RouterProvider router={platformRouter} />);
    expect(await screen.findByText("门店入驻审核列表")).toBeInTheDocument();
    platformRender.unmount();

    const institutionRouter = createMemoryRouter(
      [{ path: "/institution", children: institutionRoutes.protectedChildren }],
      { initialEntries: ["/institution/store/applications"] },
    );
    renderWithClient(<RouterProvider router={institutionRouter} />);
    expect(await screen.findByText("我的门店申请")).toBeInTheDocument();
  });
});

function renderList(initialEntry = "/platform/identity-reviews") {
  return renderWithClient(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/platform/identity-reviews" element={<IdentityReviewListPage />} />
        <Route path="/platform/login" element={<div>LOGIN TARGET</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderDetail() {
  return renderWithClient(
    <MemoryRouter initialEntries={["/platform/identity-reviews/21"]}>
      <Routes>
        <Route path="/platform/identity-reviews/:userId" element={<IdentityReviewDetailPage />} />
        <Route path="/platform/identity-reviews" element={<div>QUEUE TARGET</div>} />
        <Route path="/platform/login" element={<div>LOGIN TARGET</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderWithClient(element: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    ...render(<QueryClientProvider client={queryClient}>{element}</QueryClientProvider>),
    queryClient,
  };
}

async function submitPassword() {
  const input = screen.getByLabelText("当前审核员密码");
  await userEvent.type(input, "CURRENT_PASSWORD_INPUT");
  await userEvent.click(screen.getByRole("button", { name: "验证并临时查看" }));
}

async function completeStepUp() {
  await submitPassword();
  expect(await screen.findByText("FULL_ID_VALUE")).toBeInTheDocument();
}

function mockStepUpAndDetail() {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(apiSuccess(stepUpData()))
    .mockResolvedValueOnce(apiSuccess(detailData()));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function mockStepUpDetailAndDecision(status: number, data: unknown) {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(apiSuccess(stepUpData()))
    .mockResolvedValueOnce(apiSuccess(detailData()))
    .mockResolvedValueOnce(status >= 400 ? apiResponse(status, data) : apiSuccess(data));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function stepUpData() {
  return {
    step_up_token: "STEP_UP_VALUE",
    token_type: "identity_review_step_up",
    expires_in: 120,
  };
}

function detailData() {
  return {
    user_id: 21,
    submission_version: 3,
    status: "submitted",
    real_name: "Review Subject",
    id_card: "FULL_ID_VALUE",
    id_card_masked: "110***********1234",
    consent_version: "CONSENT_V1",
    submitted_at: "2026-08-10T01:02:03Z",
  };
}

function queueItem(extra: Record<string, unknown> = {}) {
  return {
    user_id: 21,
    submission_version: 3,
    id_card_masked: "110***********1234",
    submitted_at: "2026-08-10T01:02:03Z",
    ...extra,
  };
}

function emptyPage() {
  return { items: [], page: 1, page_size: 20, total: 0 };
}

function mockApi(status: number, data: unknown) {
  const body = status >= 400 ? data : { code: 0, message: "ok", data };
  const fetchMock = vi.fn().mockResolvedValue(apiResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function apiSuccess(data: unknown) {
  return apiResponse(200, { code: 0, message: "ok", data });
}

function apiResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
