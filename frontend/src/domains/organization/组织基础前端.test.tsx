import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { institutionRoutes } from "@/domains/institution/routes";
import { platformRoutes } from "@/domains/platform/routes";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import {
  createOrganization,
  deactivateOrganization,
  getMyOrganization,
  listOrganizationAdminCandidates,
  listOrganizationTenants,
  listOrganizationTree,
  patchOrganization,
  reorderOrganizationChildren,
} from "./组织基础接口";
import { InstitutionOrganizationPage } from "./pages/机构组织资料页";
import { PlatformOrganizationPage } from "./pages/平台组织治理页";

describe("Organization Foundation Frontend V1", () => {
  beforeEach(() => {
    setCurrentUser({ id: 1, role: USER_ROLES.superAdmin, tenant_id: null, org_id: null });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("consumes the four-level governance tree without tenant or administrator PII", async () => {
    const fetchMock = mockApi(200, { items: treeData() });

    const result = await listOrganizationTree(true);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/platform/organizations/tree?include_archived=true",
      expect.any(Object),
    );
    expect(result[0].children[0].children[0].children[0].org_type).toBe("county");
    expect(JSON.stringify(result)).not.toContain("forbidden-contact");
  });

  it("maps the real tree response envelope and canonical headquarter enum", async () => {
    mockApi(200, { items: treeData() });
    const result = await listOrganizationTree(false);
    expect(result[0].org_type).toBe("headquarter");
  });

  it.each([USER_ROLES.superAdmin, USER_ROLES.provinceAdmin, USER_ROLES.cityAdmin])(
    "allows platform read access for %s",
    async (role) => {
      setCurrentUser({ id: 2, role, tenant_id: null, org_id: role === USER_ROLES.superAdmin ? null : 11 });
      mockPlatformReadApis();
      const router = platformRouter("/platform/organizations");
      renderWithClient(<RouterProvider router={router} />);
      expect(await screen.findByRole("heading", { name: "组织治理树" })).toBeInTheDocument();
    },
  );

  it("rejects org_admin at the platform route boundary", async () => {
    setCurrentUser({ id: 3, role: USER_ROLES.orgAdmin, tenant_id: 9, org_id: 11 });
    const router = platformRouter("/platform/organizations");
    renderWithClient(<RouterProvider router={router} />);
    expect(await screen.findByText("FORBIDDEN TARGET")).toBeInTheDocument();
    expect(screen.queryByText("组织治理树")).not.toBeInTheDocument();
  });

  it("renders canonical, legacy, inactive and archived states but no rich prototype fields", async () => {
    mockPlatformReadApis(treeData({ includeStates: true }));
    renderPlatform();

    expect(await screen.findByText("华东总部")).toBeInTheDocument();
    expect(screen.getByText("兼容只读")).toBeInTheDocument();
    expect(screen.getByText("已停用")).toBeInTheDocument();
    expect(screen.getByText("已归档")).toBeInTheDocument();
    expect(screen.queryByText("forbidden-contact")).not.toBeInTheDocument();
    expect(screen.queryByText("营收")).not.toBeInTheDocument();
    expect(screen.queryByText("删除组织")).not.toBeInTheDocument();
  });

  it.each([USER_ROLES.provinceAdmin, USER_ROLES.cityAdmin])("keeps %s strictly read-only", async (role) => {
    setCurrentUser({ id: 4, role, tenant_id: null, org_id: 11 });
    mockPlatformReadApis();
    renderPlatform();
    expect(await screen.findByText("华东总部")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建下级组织" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "修改组织" })).not.toBeInTheDocument();
  });

  it("uses opaque keyset cursors and never invents totals", async () => {
    const fetchMock = mockApi(200, tenantPage("OPAQUE_NEXT"));

    const result = await listOrganizationTenants(11, {
      include_descendants: true,
      cursor: "OPAQUE_CURRENT",
      page_size: 20,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/platform/organizations/11/tenants?include_descendants=true&cursor=OPAQUE_CURRENT&page_size=20",
      expect.any(Object),
    );
    expect(result.next_cursor).toBe("OPAQUE_NEXT");
    expect(result).not.toHaveProperty("total");
  });

  it("renders current-node tenant fields, descendants toggle, and batch navigation without page counts", async () => {
    mockPlatformReadApis();
    renderPlatform();

    expect(await screen.findByText("康邻门店 A")).toBeInTheDocument();
    expect(screen.getByText("TENANT-A")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一批" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "上一批" })).toBeDisabled();
    expect(screen.queryByText(/^\d+\s*\/\s*\d+$/)).not.toBeInTheDocument();
    expect(screen.queryByText("forbidden-contact")).not.toBeInTheDocument();
  });

  it("submits the exact create contract including the parent version", async () => {
    const fetchMock = mockApi(200, detailData());
    await createOrganization({
      org_name: "测试省公司",
      org_code: "TEST-P",
      org_type: "province",
      parent_id: 11,
      parent_expected_version: 7,
      admin_user_id: 99,
    });
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/platform/organizations");
    expect(JSON.parse(String(options.body))).toEqual({
      org_name: "测试省公司",
      org_code: "TEST-P",
      org_type: "province",
      parent_id: 11,
      parent_expected_version: 7,
      admin_user_id: 99,
    });
  });

  it.each([
    ["omitted", { org_name: "新名称", expected_version: 7 }],
    ["unbind", { admin_user_id: null, expected_version: 7 }],
    ["bind", { admin_user_id: 99, expected_version: 7 }],
  ])("preserves PATCH administrator three-state semantics: %s", async (_label, payload) => {
    const fetchMock = mockApi(200, detailData());
    await patchOrganization(11, payload);
    expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toEqual(payload);
  });

  it("sends complete direct children and both parent and child versions for atomic ordering", async () => {
    const fetchMock = mockApi(200, detailData());
    await reorderOrganizationChildren(11, {
      parent_expected_version: 7,
      items: [
        { organization_id: 21, expected_version: 3, sort_order: 1 },
        { organization_id: 22, expected_version: 5, sort_order: 2 },
      ],
    });
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/platform/organizations/11/children/order");
    expect(JSON.parse(String(options.body))).toEqual({
      parent_expected_version: 7,
      items: [
        { organization_id: 21, expected_version: 3, sort_order: 1 },
        { organization_id: 22, expected_version: 5, sort_order: 2 },
      ],
    });
  });

  it("sends the fixed deactivate reason_code and exact state payload", async () => {
    const fetchMock = mockApi(200, {
      id: 21,
      status: "inactive",
      version: 8,
      updated_at: "2026-08-10T12:00:00Z",
    });
    await deactivateOrganization(21, { expected_version: 7, reason_code: "PLATFORM_GOVERNANCE" });
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/platform/organizations/21/deactivate");
    expect(JSON.parse(String(options.body))).toEqual({
      expected_version: 7,
      reason_code: "PLATFORM_GOVERNANCE",
    });
  });

  it("consumes paged administrator candidates without retaining extra PII fields", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        apiSuccess({
          items: [
            {
              user_id: 91,
              display_name: "虚构候选甲",
              role: "province_admin",
              assignment_status: "unassigned",
              phone: "forbidden",
            },
          ],
          next_cursor: "NEXT_CANDIDATE",
        }),
      )
      .mockResolvedValueOnce(
        apiSuccess({
          items: [
            {
              user_id: 92,
              display_name: "虚构候选乙",
              role: "province_admin",
              assignment_status: "assigned_to_current",
              email: "forbidden",
            },
          ],
          next_cursor: null,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const result = await listOrganizationAdminCandidates(21);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/platform/organizations/21/admin-candidates?page_size=100");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v1/platform/organizations/21/admin-candidates?page_size=100&cursor=NEXT_CANDIDATE",
    );
    expect(result).toEqual([
      { user_id: 91, display_name: "虚构候选甲", role: "province_admin", assignment_status: "unassigned" },
      {
        user_id: 92,
        display_name: "虚构候选乙",
        role: "province_admin",
        assignment_status: "assigned_to_current",
      },
    ]);
    expect(JSON.stringify(result)).not.toContain("forbidden");
  });

  it("ignores a late administrator candidate response after the panel lifecycle changes", async () => {
    let resolveFirst: ((response: Response) => void) | undefined;
    let resolveSecond: ((response: Response) => void) | undefined;
    const first = new Promise<Response>((resolve) => {
      resolveFirst = resolve;
    });
    const second = new Promise<Response>((resolve) => {
      resolveSecond = resolve;
    });
    let candidateCalls = 0;
    const fetchMock = vi.fn((url: string) => {
      if (url.includes("/tree")) return Promise.resolve(apiSuccess({ items: treeData() }));
      if (url.includes("/tenants")) return Promise.resolve(apiSuccess(tenantPage(null)));
      if (url.includes("/admin-candidates")) {
        candidateCalls += 1;
        return candidateCalls === 1 ? first : second;
      }
      return Promise.resolve(
        apiSuccess(detailData({ id: 21, parent_id: 11, org_name: "浙江省", org_type: "province" })),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPlatform();

    await screen.findByRole("button", { name: "管理员绑定" });
    await userEvent.click(screen.getByRole("button", { name: "管理员绑定" }));
    await userEvent.click(screen.getByRole("button", { name: "关闭" }));
    await userEvent.click(screen.getByRole("button", { name: "管理员绑定" }));

    resolveFirst?.(
      apiSuccess({
        items: [
          {
            user_id: 91,
            display_name: "过期虚构候选",
            role: "province_admin",
            assignment_status: "unassigned",
          },
        ],
        next_cursor: null,
      }),
    );
    await waitFor(() => expect(candidateCalls).toBe(2));
    expect(screen.queryByText(/过期虚构候选/)).not.toBeInTheDocument();

    resolveSecond?.(apiSuccess({ items: [], next_cursor: null }));
  });

  it("maps the real /organizations/me path shape including legacy status", async () => {
    mockApi(200, {
      ...institutionData(),
      compatibility_mode: "legacy",
      organization_path: [
        {
          id: 11,
          org_code: "LEGACY",
          org_name: "历史组织",
          org_type: "platform",
          status: "disabled",
          compatibility_mode: "legacy",
        },
      ],
    });
    const result = await getMyOrganization();
    expect(result.compatibility_mode).toBe("legacy");
    expect(result.organization_path[0]).toEqual({
      organization_id: 11,
      org_code: "LEGACY",
      org_name: "历史组织",
      org_type: "platform",
      status: "disabled",
      compatibility_mode: "legacy",
    });
  });

  it("does not automatically replay a 409 mutation and refreshes read state", async () => {
    let mutationCalls = 0;
    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith("/deactivate")) {
        mutationCalls += 1;
        return Promise.resolve(apiResponse(409, { detail: "internal-write-detail" }));
      }
      if (url.includes("/tree")) return Promise.resolve(apiSuccess({ items: treeData() }));
      if (url.includes("/tenants")) return Promise.resolve(apiSuccess(tenantPage("OPAQUE_NEXT")));
      return Promise.resolve(
        apiSuccess(detailData({ id: 21, parent_id: 11, org_name: "浙江省", org_type: "province" })),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPlatform();

    await screen.findByText("康邻门店 A");
    await userEvent.click(screen.getByRole("button", { name: "浙江省" }));
    await screen.findByRole("button", { name: "停用组织" });
    await userEvent.click(screen.getByRole("button", { name: "停用组织" }));
    expect(await screen.findByText("组织版本或状态已变化，已刷新最新数据，请确认后重试。")).toBeInTheDocument();
    expect(mutationCalls).toBe(1);
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/tree")).length).toBeGreaterThan(1),
    );
  });

  it("loads archived siblings before enabling atomic child ordering", async () => {
    const visibleChild = node(21, "可见省公司", "province", "active", "canonical", []);
    const archivedChild = node(22, "归档省公司", "province", "archived", "canonical", []);
    const visibleTree = [node(11, "华东总部", "headquarter", "active", "canonical", [visibleChild])];
    const completeTree = [node(11, "华东总部", "headquarter", "active", "canonical", [visibleChild, archivedChild])];
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      if (url.includes("/tree?include_archived=true")) return Promise.resolve(apiSuccess({ items: completeTree }));
      if (url.includes("/tree")) return Promise.resolve(apiSuccess({ items: visibleTree }));
      if (url.includes("/tenants")) return Promise.resolve(apiSuccess(tenantPage(null)));
      if (url.endsWith("/children/order") && options?.method === "PUT") {
        return Promise.resolve(apiSuccess(detailData()));
      }
      return Promise.resolve(apiSuccess(detailData()));
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPlatform();

    const orderButton = await screen.findByRole("button", { name: "调整同级排序" });
    expect(orderButton).toBeDisabled();
    expect(screen.getByText("排序前请先加载已归档历史节点，确保提交完整的直接下级集合。")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "包含已归档历史节点" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "调整同级排序" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "调整同级排序" }));
    expect(await screen.findAllByText("归档省公司")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "确认排序" }));

    const orderCall = fetchMock.mock.calls.find(
      ([url, options]) =>
        String(url).endsWith("/children/order") && (options as RequestInit | undefined)?.method === "PUT",
    );
    if (!orderCall) throw new Error("Expected atomic ordering request");
    expect(JSON.parse(String((orderCall[1] as RequestInit).body))).toEqual({
      parent_expected_version: 7,
      items: [
        { organization_id: 21, expected_version: 7, sort_order: 0 },
        { organization_id: 22, expected_version: 7, sort_order: 1 },
      ],
    });
  });

  it("keeps legacy and archived nodes mutation-free", async () => {
    mockPlatformReadApis(
      treeData({ rootMode: "legacy", rootStatus: "archived" }),
      detailData({ compatibility_mode: "legacy", status: "archived" }),
    );
    renderPlatform();
    expect(await screen.findByText("只读节点不允许治理操作")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "停用组织" })).not.toBeInTheDocument();
  });

  it("renders assigned institution organization information with only approved fields", async () => {
    mockApi(200, institutionData());
    renderInstitution();
    expect(await screen.findByText("机构组织资料")).toBeInTheDocument();
    expect(screen.getByText("康邻门店 A")).toBeInTheDocument();
    expect(screen.getByText("华东总部 / 浙江省 / 杭州市")).toBeInTheDocument();
    expect(screen.queryByText("forbidden-contact")).not.toBeInTheDocument();
    expect(screen.queryByText("资料完整度")).not.toBeInTheDocument();
    expect(screen.queryByText("安全设置")).not.toBeInTheDocument();
  });

  it("renders the unassigned institution state for null organization and an empty path", async () => {
    mockApi(200, {
      ...institutionData(),
      assignment_status: "unassigned",
      organization_id: null,
      organization_path: [],
    });
    renderInstitution();
    expect((await screen.findAllByText("暂未分配平台组织")).length).toBeGreaterThanOrEqual(1);
  });

  it.each([
    [400, "组织分页游标已失效，请刷新后重试。"],
    [403, "当前账号无权访问组织信息。"],
    [404, "组织信息不存在或不在当前访问范围。"],
    [422, "组织请求字段或层级不符合合同，请检查后重试。"],
    [503, "组织服务暂时不可用，请稍后重试。"],
  ])("maps platform %s to a safe retryable state", async (status, message) => {
    mockApi(status, { detail: "internal-sensitive-detail" });
    renderPlatform();
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText("internal-sensitive-detail")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("clears the session and redirects on platform 401", async () => {
    mockApi(401, { detail: "Unauthorized" });
    renderPlatform();
    expect(await screen.findByText("LOGIN TARGET")).toBeInTheDocument();
  });

  it("keeps P1 and Identity Review routes renderable", async () => {
    mockApi(200, { items: [], page: 1, page_size: 20, total: 0 });
    const platform = platformRouter("/platform/identity-reviews");
    const platformRender = renderWithClient(<RouterProvider router={platform} />);
    expect(await screen.findByText("暂无待审核实名认证")).toBeInTheDocument();
    platformRender.unmount();

    mockApi(200, { items: [], page: 1, page_size: 20, total: 0 });
    const institution = createMemoryRouter([{ path: "/institution", children: institutionRoutes.protectedChildren }], {
      initialEntries: ["/institution/store/applications"],
    });
    renderWithClient(<RouterProvider router={institution} />);
    expect(await screen.findByText("我的门店申请")).toBeInTheDocument();
  });
});

function renderPlatform() {
  return renderWithClient(
    <MemoryRouter initialEntries={["/platform/organizations"]}>
      <Routes>
        <Route path="/platform/organizations" element={<PlatformOrganizationPage />} />
        <Route path="/platform/organizations/:organizationId" element={<PlatformOrganizationPage />} />
        <Route path="/platform/login" element={<div>LOGIN TARGET</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderInstitution() {
  return renderWithClient(
    <MemoryRouter initialEntries={["/institution/organization"]}>
      <Routes>
        <Route path="/institution/organization" element={<InstitutionOrganizationPage />} />
        <Route path="/institution/login" element={<div>LOGIN TARGET</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function platformRouter(initialEntry: string) {
  return createMemoryRouter(
    [
      { path: "/platform", children: platformRoutes.protectedChildren },
      { path: "/403", element: <div>FORBIDDEN TARGET</div> },
      { path: "/platform/login", element: <div>LOGIN TARGET</div> },
    ],
    { initialEntries: [initialEntry] },
  );
}

function renderWithClient(element: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{element}</QueryClientProvider>);
}

function mockPlatformReadApis(tree = treeData(), detail = detailData()) {
  const fetchMock = vi.fn((url: string) => {
    if (url.includes("/tree")) return Promise.resolve(apiSuccess({ items: tree }));
    if (url.includes("/tenants")) return Promise.resolve(apiSuccess(tenantPage("OPAQUE_NEXT")));
    return Promise.resolve(apiSuccess(detail));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function treeData(options: { includeStates?: boolean; rootMode?: string; rootStatus?: string } = {}) {
  const county = node(41, "滨江区", "county", options.includeStates ? "archived" : "active", "canonical", []);
  const city = node(31, "杭州市", "city", options.includeStates ? "inactive" : "active", "canonical", [county]);
  const province = node(21, "浙江省", "province", "active", options.includeStates ? "legacy" : "canonical", [city]);
  return [
    node(11, "华东总部", "headquarter", options.rootStatus ?? "active", options.rootMode ?? "canonical", [province]),
  ];
}

function node(id: number, name: string, type: string, status: string, mode: string, children: unknown[]) {
  return {
    id,
    parent_id: id === 11 ? null : id - 10,
    org_code: `ORG-${id}`,
    org_name: name,
    org_type: type,
    status,
    compatibility_mode: mode,
    sort_order: 1,
    version: 7,
    children,
    contact: "forbidden-contact",
  };
}

function detailData(overrides: Record<string, unknown> = {}) {
  return {
    id: 11,
    parent_id: null,
    org_code: "ORG-11",
    org_name: "华东总部",
    org_type: "headquarter",
    status: "active",
    compatibility_mode: "canonical",
    path_codes: ["ORG-11"],
    path_names: ["华东总部"],
    sort_order: 1,
    version: 7,
    admin_user_id: 99,
    ...overrides,
  };
}

function tenantPage(nextCursor: string | null) {
  return {
    items: [
      {
        tenant_id: 501,
        tenant_code: "TENANT-A",
        name: "康邻门店 A",
        type: "store",
        province: "浙江省",
        city: "杭州市",
        district: "滨江区",
        grade: "A",
        status: "active",
        organization_id: 11,
        organization_path_codes: ["ORG-11"],
        contact: "forbidden-contact",
      },
    ],
    next_cursor: nextCursor,
  };
}

function institutionData() {
  return {
    tenant_id: 501,
    tenant_code: "TENANT-A",
    tenant_name: "康邻门店 A",
    tenant_type: "store",
    tenant_status: "active",
    assignment_status: "assigned",
    organization_id: 31,
    organization_path: [
      {
        id: 11,
        org_code: "ORG-11",
        org_name: "华东总部",
        org_type: "headquarter",
        status: "active",
        compatibility_mode: "canonical",
      },
      {
        id: 21,
        org_code: "ORG-21",
        org_name: "浙江省",
        org_type: "province",
        status: "active",
        compatibility_mode: "canonical",
      },
      {
        id: 31,
        org_code: "ORG-31",
        org_name: "杭州市",
        org_type: "city",
        status: "active",
        compatibility_mode: "canonical",
      },
    ],
    compatibility_mode: "canonical",
    contact: "forbidden-contact",
  };
}

function mockApi(status: number, data: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(status >= 400 ? apiResponse(status, data) : apiSuccess(data));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function apiSuccess(data: unknown) {
  return apiResponse(200, { code: 0, message: "ok", data });
}

function apiResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
