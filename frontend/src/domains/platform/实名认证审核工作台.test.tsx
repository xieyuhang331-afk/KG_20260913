import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { listIdentityReviews } from "./实名认证审核接口";
import { IdentityReviewDetailView, IdentityReviewDetailPage } from "./pages/实名认证审核详情页";
import { IdentityReviewListPage } from "./pages/实名认证审核列表页";
import { platformRoutes } from "./routes";

describe("Platform Identity Review Workbench", () => {
  beforeEach(() => {
    setCurrentUser({ id: 1, role: USER_ROLES.superAdmin, tenant_id: null, org_id: null });
  });

  it("requests only the submitted queue and maps only masked identity fields", async () => {
    const fetchMock = mockApi(200, {
      items: [
        {
          user_id: 21,
          submission_version: 3,
          id_card_masked: "110***********1234",
          unexpected_sensitive_field: "forbidden-full-value",
          submitted_at: "2026-08-10T01:02:03Z",
        },
      ],
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
      items: [
        {
          user_id: 21,
          submission_version: 3,
          id_card_masked: "110***********1234",
          unexpected_sensitive_field: "forbidden-full-value",
          submitted_at: "2026-08-10T01:02:03Z",
        },
      ],
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
    mockApi(200, { items: [], page: 1, page_size: 20, total: 0 });
    renderList();
    expect(await screen.findByText("暂无待审核实名认证")).toBeInTheDocument();
  });

  it("handles 503 safely and retries on demand", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(apiResponse(503, { detail: "internal-sensitive-detail" }))
      .mockResolvedValueOnce(
        apiResponse(200, { code: 0, message: "ok", data: { items: [], page: 1, page_size: 20, total: 0 } }),
      );
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
  ])("maps %s to a safe queue error", async (status, message) => {
    mockApi(status, { detail: "internal-sensitive-detail" });
    renderList();
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText("internal-sensitive-detail")).not.toBeInTheDocument();
  });

  it("clears the frontend session and redirects to platform login on 401", async () => {
    mockApi(401, { detail: "Unauthorized" });
    renderList();
    expect(await screen.findByText("LOGIN TARGET")).toBeInTheDocument();
  });

  it("rejects a non-super-admin at the route boundary", async () => {
    setCurrentUser({ id: 2, role: USER_ROLES.provinceAdmin, tenant_id: null, org_id: null });
    const router = createMemoryRouter(
      [
        {
          path: "/platform",
          children: platformRoutes.protectedChildren,
        },
        { path: "/403", element: <div>FORBIDDEN TARGET</div> },
      ],
      { initialEntries: ["/platform/identity-reviews"] },
    );

    render(<RouterProvider router={router} />);
    expect(await screen.findByText("FORBIDDEN TARGET")).toBeInTheDocument();
    expect(screen.queryByText("实名认证审核工作台")).not.toBeInTheDocument();
  });

  it("keeps detail PII hidden and does not call a detail API before step-up", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/platform/identity-reviews/21"]}>
        <Routes>
          <Route path="/platform/identity-reviews/:userId" element={<IdentityReviewDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("敏感身份信息默认隐藏")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "等待二次认证能力" })).toBeDisabled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("provides confirmation skeletons without enabling sensitive decisions", async () => {
    render(
      <MemoryRouter>
        <IdentityReviewDetailView userId={21} onRefresh={vi.fn()} />
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole("button", { name: "审核通过" }));
    expect(screen.getByRole("dialog", { name: "确认审核通过" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "等待后端合同后提交通过" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    await userEvent.click(screen.getByRole("button", { name: "审核驳回" }));
    expect(screen.getByRole("dialog", { name: "确认审核驳回" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "等待后端合同后提交驳回" })).toBeDisabled();
  });

  it("offers a deterministic refresh path for a future 409 conflict", async () => {
    const onRefresh = vi.fn();
    render(
      <MemoryRouter>
        <IdentityReviewDetailView userId={21} conflict onRefresh={onRefresh} />
      </MemoryRouter>,
    );

    await userEvent.click(screen.getByRole("button", { name: "刷新审核状态" }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("disables both decisions while a future mutation is submitting", () => {
    render(
      <MemoryRouter>
        <IdentityReviewDetailView userId={21} isSubmitting onRefresh={vi.fn()} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "审核通过" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "审核驳回" })).toBeDisabled();
  });
});

function renderList(initialEntry = "/platform/identity-reviews") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/platform/identity-reviews" element={<IdentityReviewListPage />} />
          <Route path="/platform/login" element={<div>LOGIN TARGET</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function mockApi(status: number, data: unknown) {
  const body = status >= 400 ? data : { code: 0, message: "ok", data };
  const fetchMock = vi.fn().mockResolvedValue(apiResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function apiResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
