import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { isValidElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { MedicalRuleGovernancePage } from "./pages/MedicalRuleGovernancePage";
import { allPlatformNavigation } from "./navigation";
import { platformRoutes } from "./routes";

describe("一期切片5医学规则治理", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    setCurrentUser(null);
  });

  it("注册规则治理列表详情路由并按正式角色开放导航", () => {
    const children = platformRoutes.protectedChildren.flatMap((route) => route.children ?? []);
    expect(children).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ path: "assessment-rule-sets" }),
        expect.objectContaining({ path: "assessment-rule-sets/:versionId" }),
      ]),
    );
    const routeGroup = platformRoutes.protectedChildren.find((route) =>
      route.children?.some((child) => child.path === "assessment-rule-sets"),
    );
    expect(isValidElement(routeGroup?.element)).toBe(true);
    if (!isValidElement<{ roles: string[] }>(routeGroup?.element)) throw new Error("规则治理路由缺少角色守卫");
    expect(routeGroup.element.props.roles).toEqual([USER_ROLES.expert, USER_ROLES.sysAdmin, USER_ROLES.superAdmin]);
    const item = allPlatformNavigation.find((candidate) => candidate.label === "医学规则治理");
    expect(item?.roles).toEqual([USER_ROLES.expert, USER_ROLES.sysAdmin, USER_ROLES.superAdmin]);
  });

  it("规则列表显示安全人员名称、状态和opaque分页而不显示内部ID", async () => {
    setCurrentUser({ id: 71, role: USER_ROLES.expert });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(success({ items: [summary("DRAFT")], next_cursor: "opaque.next" })),
    );
    renderPage("/platform/assessment-rule-sets", "/platform/assessment-rule-sets");

    expect(await screen.findByText("医学专家甲")).toBeInTheDocument();
    expect(screen.getByText("草稿")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一批" })).toBeEnabled();
    expect(screen.queryByText(/^71$/)).not.toBeInTheDocument();
    expect(screen.queryByText("opaque.next")).not.toBeInTheDocument();
  });

  it("旧cursor失效后由用户返回首页，且恢复请求不再携带cursor", async () => {
    setCurrentUser({ id: 71, role: USER_ROLES.expert });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success({ items: [summary("DRAFT")], next_cursor: "legacy.cursor" }))
      .mockResolvedValueOnce(failure(422, "INVALID_REQUEST"))
      .mockResolvedValueOnce(success({ items: [summary("PUBLISHED", 3, "APPROVED")], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage("/platform/assessment-rule-sets", "/platform/assessment-rule-sets");

    await userEvent.click(await screen.findByRole("button", { name: "下一批" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("分页凭据");
    await userEvent.click(screen.getByRole("button", { name: "返回首页重新查询" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("cursor=legacy.cursor");
    expect(String(fetchMock.mock.calls[2]?.[0])).not.toContain("cursor=");
    expect(screen.queryByRole("button", { name: "返回首页重新查询" })).not.toBeInTheDocument();
  });

  it("expert可在草稿详情修改依据并提交审核，unknown结果复用同一Key和Body确认", async () => {
    setCurrentUser({ id: 71, role: USER_ROLES.expert });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success(detail("DRAFT", 2)))
      .mockResolvedValueOnce(failure(503, "COMMIT_OUTCOME_UNKNOWN"))
      .mockResolvedValueOnce(success(detail("DRAFT", 3)));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    renderPage(`/platform/assessment-rule-sets/${ruleSetId}`, "/platform/assessment-rule-sets/:versionId");

    await userEvent.clear(await screen.findByLabelText("审批依据引用"));
    await userEvent.type(screen.getByLabelText("审批依据引用"), "受控双签依据V2");
    await userEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("提交结果暂时无法确认");
    await userEvent.click(screen.getByRole("button", { name: "查询并确认原请求结果" }));

    const mutations = fetchMock.mock.calls.filter(([, options]) => options?.method === "PATCH");
    expect(mutations).toHaveLength(2);
    expect(new Headers(mutations[0]?.[1]?.headers).get("Idempotency-Key")).toBe(
      new Headers(mutations[1]?.[1]?.headers).get("Idempotency-Key"),
    );
    expect(mutations[0]?.[1]?.body).toBe(mutations[1]?.[1]?.body);
  });

  it("sys_admin只能执行发布和生命周期治理而不能提交医学审核", async () => {
    setCurrentUser({ id: 72, role: USER_ROLES.sysAdmin });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(detail("IN_REVIEW", 4, "APPROVED"))));
    renderPage(`/platform/assessment-rule-sets/${ruleSetId}`, "/platform/assessment-rule-sets/:versionId");

    expect(await screen.findByRole("button", { name: "发布规则版本" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交医学审核" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批准" })).not.toBeInTheDocument();
  });

  it("expert在待审核状态仅使用正式批准或补正原因码", async () => {
    setCurrentUser({ id: 73, role: USER_ROLES.expert });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success(detail("IN_REVIEW", 4)))
      .mockResolvedValueOnce(success(detail("IN_REVIEW", 5, "APPROVED")));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    renderPage(`/platform/assessment-rule-sets/${ruleSetId}`, "/platform/assessment-rule-sets/:versionId");

    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      expected_version: 4,
      decision: "APPROVE",
      reason_code: "MEDICAL_CONTENT_APPROVED",
    });
  });

  it("未知状态fail-closed，不提供任何治理写操作", async () => {
    setCurrentUser({ id: 73, role: USER_ROLES.expert });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(detail("UNKNOWN", 4))));
    renderPage(`/platform/assessment-rule-sets/${ruleSetId}`, "/platform/assessment-rule-sets/:versionId");

    expect(await screen.findByText("状态待核对")).toBeInTheDocument();
    expect(screen.getByText("当前状态没有可执行操作，页面保持只读。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /保存|提交|批准|补正|发布|暂停|恢复|退役/ })).not.toBeInTheDocument();
  });

  it("503保持依赖不可用语义", async () => {
    setCurrentUser({ id: 73, role: USER_ROLES.expert });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(failure(503, "DEPENDENCY_UNAVAILABLE")));
    renderPage(`/platform/assessment-rule-sets/${ruleSetId}`, "/platform/assessment-rule-sets/:versionId");

    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂时不可用");
    expect(screen.queryByRole("button", { name: /批准|发布|暂停|恢复|退役/ })).not.toBeInTheDocument();
  });
});

const ruleSetId = "0198d6a1-1111-7abc-8000-000000000850";

function renderPage(path: string, routePath: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<MedicalRuleGovernancePage />} path={routePath} />
      </Routes>
    </MemoryRouter>,
  );
}

function summary(status: string, version = 2, approvalState = "PENDING") {
  return {
    rule_set_version_id: ruleSetId,
    version_no: 2,
    status,
    module_metadata: ["BLOOD_PRESSURE_CARDIOVASCULAR"],
    author_ref: { public_user_ref: "usr_synthetic_author", display_name: "医学专家甲", role_label: "EXPERT" },
    reviewer_ref: null,
    approval_state: approvalState,
    effective_from: null,
    suspended_at: null,
    retired_at: null,
    version,
  };
}

function detail(status: string, version: number, approvalState = "PENDING") {
  return {
    ...summary(status, version, approvalState),
    rule_set_code: "CN_ADULT_BASELINE_V1",
    typed_rule_payload: {
      schema_version: "SLICE5_MEDICAL_RULE_PAYLOAD_V1",
      rule_set_code: "CN_ADULT_BASELINE_V1",
      modules: [],
    },
    approval_evidence_ref: "受控双签依据",
  };
}

function success(data: unknown) {
  return new Response(JSON.stringify({ data }), { status: 200, headers: { "Content-Type": "application/json" } });
}

function failure(status: number, code: string) {
  return new Response(JSON.stringify({ code }), { status, headers: { "Content-Type": "application/json" } });
}
