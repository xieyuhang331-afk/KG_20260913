import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearAccessToken, getAccessToken, setAccessToken } from "@/shared/auth/tokenStorage";
import { getAuthSnapshot, setCurrentUser } from "@/shared/auth/authStore";
import { listMemberIdentityReviews } from "./实名认证审核接口";
import { IdentityReviewDetailPage } from "./pages/实名认证审核详情页";
import { IdentityReviewListPage } from "./pages/实名认证审核列表页";

const reviewId = "0198b963-38f0-7d7d-8000-000000000021";
const revisionId = "0198b963-38f0-7d7d-8000-000000000022";
const syntheticFullId = ["11010519491231002", "X"].join("");

describe("Platform Member Identity Review Workbench", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    clearAccessToken();
  });

  it("使用 UUIDv7 与 cursor 请求新队列", async () => {
    const fetchMock = mockSequence(success(page([summary()])));
    const value = await listMemberIdentityReviews({ status: "INSTITUTION_CHECKED", cursor: "opaque", limit: 20 });
    expect(value.items[0]?.review_id).toBe(reviewId);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/api/v1/platform/member-identity-reviews?");
    expect(fetchMock.mock.calls[0]?.[0]).toContain("cursor=opaque");
    expect(fetchMock.mock.calls[0]?.[0]).not.toContain("/api/v1/reviews/");
  });

  it("队列只渲染脱敏证件和 UUID 审核标识", async () => {
    mockSequence(success(page([{ ...summary(), unexpected_full_value: "forbidden" }])));
    renderList();
    expect(await screen.findByText("110***********1234")).toBeInTheDocument();
    expect(screen.queryByText("forbidden")).not.toBeInTheDocument();
    expect(screen.getByText(/0198b963…000021/)).toBeInTheDocument();
    expect(screen.getByText("机构核验通过，待平台终审")).toBeInTheDocument();
    expect(screen.queryByText("INSTITUTION_CHECKED")).not.toBeInTheDocument();
  });

  it("提供空状态与恢复动作", async () => {
    mockSequence(success(page([])));
    renderList();
    expect(await screen.findByText("暂无待审核实名认证")).toBeInTheDocument();
  });

  it("503 不显示原始 detail 并允许重新加载", async () => {
    const fetchMock = mockSequence(failure(503, "DEPENDENCY_UNAVAILABLE", "internal detail"), success(page([])));
    renderList();
    expect(await screen.findByText("服务暂时不可用，请稍后重试")).toBeInTheDocument();
    expect(screen.queryByText("internal detail")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await screen.findByText("暂无待审核实名认证");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("401 清除前端会话且只显示安全文案", async () => {
    setAccessToken("ephemeral-token");
    setCurrentUser({ id: 1, role: "super_admin" });
    mockSequence(failure(401, "ACTOR_CURRENTNESS_FORBIDDEN", "private"));
    renderList();
    expect(await screen.findByText("登录状态已失效，请重新登录")).toBeInTheDocument();
    expect(getAccessToken()).toBeNull();
    expect(getAuthSnapshot().currentUser).toBeNull();
  });

  it("未重认证前完整身份始终隐藏", async () => {
    mockSequence(success(detail()));
    renderDetail();
    expect(await screen.findByText("110***********1234")).toBeInTheDocument();
    expect(screen.queryByText(syntheticFullId)).not.toBeInTheDocument();
    expect(screen.getByText("机构核验通过，待平台终审")).toBeInTheDocument();
    expect(screen.getByText("当前实名材料")).toBeInTheDocument();
    expect(screen.getByText("第 2 版")).toBeInTheDocument();
    expect(screen.getByText("机构已完成线下实名核验")).toBeInTheDocument();
    expect(screen.queryByText("OFFLINE_IDENTITY_CHECKED")).not.toBeInTheDocument();
    expect(screen.queryByText(/Revision/)).not.toBeInTheDocument();
  });

  it("PII 请求只在 body 传当前密码并使用 no-store", async () => {
    const fetchMock = mockSequence(success(detail()), success(pii()));
    renderDetail();
    await screen.findByText("110***********1234");
    await userEvent.type(screen.getByLabelText("当前审核员密码"), "ephemeral-password");
    await userEvent.click(screen.getByRole("button", { name: "临时查看" }));
    expect(await screen.findByText(syntheticFullId)).toBeInTheDocument();
    const [url, options] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(url).not.toContain("ephemeral-password");
    expect(options.cache).toBe("no-store");
    expect(JSON.parse(String(options.body))).toEqual({
      current_password: "ephemeral-password",
      reason_code: "PLATFORM_IDENTITY_REVIEW",
    });
    expect(screen.queryByLabelText("当前审核员密码")).not.toBeInTheDocument();
  });

  it("60 秒后自动清除完整身份", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockSequence(success(detail()), success(pii()));
    renderDetail();
    await screen.findByText("110***********1234");
    await userEvent.type(screen.getByLabelText("当前审核员密码"), "temporary");
    await userEvent.click(screen.getByRole("button", { name: "临时查看" }));
    expect(await screen.findByText(syntheticFullId)).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_001));
    expect(screen.queryByText(syntheticFullId)).not.toBeInTheDocument();
  });

  it("审核员可立即隐藏，离开页面也会清除", async () => {
    mockSequence(success(detail()), success(pii()));
    const view = renderDetail();
    await screen.findByText("110***********1234");
    await userEvent.type(screen.getByLabelText("当前审核员密码"), "temporary");
    await userEvent.click(screen.getByRole("button", { name: "临时查看" }));
    await screen.findByText(syntheticFullId);
    await userEvent.click(screen.getByRole("button", { name: "立即隐藏" }));
    expect(screen.queryByText(syntheticFullId)).not.toBeInTheDocument();
    view.unmount();
  });

  it("离页会中止尚未完成的 PII 响应并阻止迟到写回", async () => {
    let resolvePii!: (value: Response) => void;
    const pendingPii = new Promise<Response>((resolve) => {
      resolvePii = resolve;
    });
    const fetchMock = mockSequence(success(detail()), pendingPii);
    const view = renderDetail();
    await screen.findByText("110***********1234");
    await userEvent.type(screen.getByLabelText("当前审核员密码"), "temporary");
    await userEvent.click(screen.getByRole("button", { name: "临时查看" }));
    const piiRequest = fetchMock.mock.calls[1]?.[1] as RequestInit;
    view.unmount();
    expect(piiRequest.signal?.aborted).toBe(true);
    resolvePii(success(pii()));
  });

  it("403 决定失败会立即清除已显示的完整身份且不显示原始错误", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    mockSequence(success(detail()), success(pii()), failure(403, "REVIEWER_CURRENTNESS_FORBIDDEN", "private detail"));
    renderDetail();
    await screen.findByText("110***********1234");
    await userEvent.type(screen.getByLabelText("当前审核员密码"), "temporary");
    await userEvent.click(screen.getByRole("button", { name: "临时查看" }));
    await screen.findByText(syntheticFullId);
    await userEvent.click(screen.getByRole("button", { name: "确认提交决定" }));
    expect(await screen.findByText("当前账号无权执行此操作")).toBeInTheDocument();
    expect(screen.queryByText(syntheticFullId)).not.toBeInTheDocument();
    expect(screen.queryByText("private detail")).not.toBeInTheDocument();
  });

  it("APPROVED 决定携带 revision_id/expected_version 且不含客户端决定时间", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    const fetchMock = mockSequence(
      success(detail()),
      success({ status: "APPROVED" }),
      success(detail({ status: "APPROVED", version: 5 })),
    );
    renderDetail();
    await screen.findByText("110***********1234");
    await userEvent.click(screen.getByRole("button", { name: "确认提交决定" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const decisionRequest = fetchMock.mock.calls[1] as [string, RequestInit];
    const body = JSON.parse(String(decisionRequest[1].body));
    expect(body).toEqual({
      revision_id: revisionId,
      decision: "APPROVED",
      reason_code: null,
      correction_fields: [],
      represented_elder_eligible: null,
      expected_version: 4,
    });
    expect(body).not.toHaveProperty("decided_at");
    expect(body).not.toHaveProperty("evidence_digest");
  });

  it("409 清除敏感状态、刷新一次且不重放决定", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    const fetchMock = mockSequence(
      success(detail()),
      success(pii()),
      failure(409, "VERSION_CONFLICT"),
      success(detail({ version: 5 })),
    );
    renderDetail();
    await screen.findByText("110***********1234");
    await userEvent.type(screen.getByLabelText("当前审核员密码"), "temporary");
    await userEvent.click(screen.getByRole("button", { name: "临时查看" }));
    await screen.findByText(syntheticFullId);
    await userEvent.click(screen.getByRole("button", { name: "确认提交决定" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(screen.queryByText(syntheticFullId)).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/decision"))).toHaveLength(1);
  });

  it("操作中禁用决定按钮以阻止重复提交", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    let resolveDecision!: (value: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      resolveDecision = resolve;
    });
    mockSequence(success(detail()), pending);
    renderDetail();
    await screen.findByText("110***********1234");
    const button = screen.getByRole("button", { name: "确认提交决定" });
    await userEvent.click(button);
    expect(button).toBeDisabled();
    resolveDecision(success({ status: "APPROVED" }));
  });
});

function renderList() {
  return render(
    <MemoryRouter>
      <IdentityReviewListPage />
    </MemoryRouter>,
  );
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={[`/platform/identity-reviews/${reviewId}`]}>
      <Routes>
        <Route path="/platform/identity-reviews/:reviewId" element={<IdentityReviewDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockSequence(...values: Array<Response | Promise<Response>>) {
  const fetchMock = vi.fn();
  for (const value of values) fetchMock.mockImplementationOnce(() => Promise.resolve(value));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function success(data: unknown) {
  return new Response(JSON.stringify({ data, request_id: reviewId }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
function failure(status: number, code: string, message = "safe") {
  return new Response(JSON.stringify({ code, message }), { status, headers: { "Content-Type": "application/json" } });
}
function page(items: unknown[]) {
  return { items, next_cursor: null };
}
function summary(overrides: object = {}) {
  return {
    review_id: reviewId,
    enrollment_id: "0198b963-38f0-7d7d-8000-000000000024",
    member_id: "0198b963-38f0-7d7d-8000-000000000025",
    mode: "SELF",
    status: "INSTITUTION_CHECKED",
    id_masked: "110***********1234",
    submitted_at: "2026-08-21T00:00:00+08:00",
    version: 4,
    ...overrides,
  };
}
function detail(overrides: object = {}) {
  return {
    ...summary(),
    current_revision_id: revisionId,
    current_revision_no: 2,
    institution_attestation: "OFFLINE_IDENTITY_CHECKED",
    correction_fields: [],
    proxy_witness_status: null,
    ...overrides,
  };
}
function pii() {
  return {
    review_id: reviewId,
    revision_id: revisionId,
    real_name: "合成用户",
    id_number: syntheticFullId,
    birth_date: "1949-12-31",
    access_id: "0198b963-38f0-7d7d-8000-000000000023",
  };
}
