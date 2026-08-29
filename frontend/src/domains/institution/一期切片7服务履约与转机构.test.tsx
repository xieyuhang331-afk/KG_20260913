import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { InstitutionShell } from "@/shells/InstitutionShell";
import { availableCaseActions, ServiceFulfillmentPage, serviceLifecycleLabel } from "./pages/ServiceFulfillmentPage";
import { availableTransferActions, ServiceTransferPage, transferStatusLabel } from "./pages/ServiceTransferPage";
import { institutionNavigation } from "./navigation";
import { institutionRoutes } from "./routes";

describe("一期切片7机构服务履约与转机构", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    setCurrentUser(null);
  });

  it("注册履约与转机构列表详情路由和正式导航", () => {
    expect(institutionRoutes.protectedChildren).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ path: "service-cases" }),
        expect.objectContaining({ path: "service-cases/:caseId/fulfillment" }),
        expect.objectContaining({ path: "service-transfers" }),
        expect.objectContaining({ path: "service-transfers/:transferId" }),
      ]),
    );
    expect(institutionNavigation.map((item) => item.label)).toEqual(expect.arrayContaining(["服务履约", "转机构接续"]));
  });

  it("org_operator保持只读且未知状态fail-closed", () => {
    expect(availableCaseActions("ACTIVE", "institution")).toEqual(["pause", "terminate"]);
    expect(availableCaseActions("UNKNOWN", "institution")).toEqual([]);
    expect(serviceLifecycleLabel("UNKNOWN")).toBe("状态待核对");
  });

  it("转机构动作严格受服务端状态约束", () => {
    expect(availableTransferActions("REQUESTED_BY_USER", "institution")).toEqual(["start-review"]);
    expect(availableTransferActions("NEW_INSTITUTION_REVIEWING", "institution")).toEqual(["reject"]);
    expect(availableTransferActions("ACCEPTED", "institution")).toEqual([]);
    expect(availableTransferActions("UNKNOWN", "institution")).toEqual([]);
    expect(transferStatusLabel("UNKNOWN")).toBe("状态待核对");
  });

  it("不会把终态或未知态显示为可恢复", () => {
    for (const status of ["COMPLETED", "TRANSFERRED", "SAFETY_TERMINATED", "UNKNOWN"]) {
      expect(availableCaseActions(status, "institution")).toEqual([]);
    }
  });

  it("org_operator可查看五阶段履约详情但不出现写操作", async () => {
    setCurrentUser({ id: 8, role: USER_ROLES.orgOperator, tenant_id: 2 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(fulfillment("ACTIVE"))));
    renderInstitutionRoute(
      `/institution/service-cases/${caseId}/fulfillment`,
      "/institution/service-cases/:caseId/fulfillment",
      <ServiceFulfillmentPage />,
    );

    expect(await screen.findByRole("heading", { name: /服务案例/ })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "D0至D28服务里程碑" })).toBeInTheDocument();
    expect(screen.getByText("D28")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "暂停服务" })).not.toBeInTheDocument();
    expect(screen.getByText(/当前角色为只读/)).toBeInTheDocument();
  });

  it("机构端页头按当前角色展示运营人员或管理员", async () => {
    setCurrentUser({ id: 8, role: USER_ROLES.orgOperator, tenant_id: 2 });
    renderInstitutionShell();

    expect(screen.getByText("机构运营人员")).toBeInTheDocument();
    expect(screen.queryByText("机构管理员")).not.toBeInTheDocument();
  });

  it("409后刷新权威详情且不自动重放暂停操作", async () => {
    setCurrentUser({ id: 9, role: USER_ROLES.orgAdmin, tenant_id: 2 });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success(fulfillment("ACTIVE")))
      .mockResolvedValueOnce(failure(409, "CASE_STATE_CONFLICT"))
      .mockResolvedValueOnce(success(fulfillment("PAUSED")));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    renderInstitutionRoute(
      `/institution/service-cases/${caseId}/fulfillment`,
      "/institution/service-cases/:caseId/fulfillment",
      <ServiceFulfillmentPage />,
    );

    await userEvent.click(await screen.findByRole("button", { name: "暂停服务" }));
    await waitFor(() => expect(screen.getByText("服务已暂停")).toBeInTheDocument());
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
    expect(screen.getByText(/数据已更新/)).toBeInTheDocument();
  });

  it("转入接受缺少服务标签目录时不允许硬编码提交", async () => {
    setCurrentUser({ id: 9, role: USER_ROLES.orgAdmin, tenant_id: 2 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(transfer("NEW_INSTITUTION_REVIEWING"))));
    renderInstitutionRoute(
      `/institution/service-transfers/${transferId}`,
      "/institution/service-transfers/:transferId",
      <ServiceTransferPage />,
    );

    expect(await screen.findByText(/服务端提供当前机构可选服务标签/)).toBeInTheDocument();
    expect(screen.getByText("健康档案摘要")).toBeInTheDocument();
    expect(screen.getByText("评估结果")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /接受转入/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "拒绝转入" })).toBeInTheDocument();
  });
});

const caseId = "0198d6a1-1111-7abc-8000-000000000711";
const transferId = "0198d6a1-1111-7abc-8000-000000000712";

function renderInstitutionRoute(path: string, routePath: string, element: ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={element} path={routePath} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderInstitutionShell() {
  return render(
    <MemoryRouter initialEntries={["/institution/service-cases"]}>
      <Routes>
        <Route element={<InstitutionShell />} path="/institution">
          <Route element={<div>服务履约内容</div>} path="service-cases" />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function fulfillment(status: string) {
  return {
    service_case_id: caseId,
    lifecycle_status: status,
    risk_flag: null,
    active_plan_id: "0198d6a1-1111-7abc-8000-000000000713",
    cycle_anchor_at: "2026-08-29T08:00:00Z",
    current_schedule_version: 1,
    milestones: ["D0", "D7", "D14", "D21", "D28"].map((code, index) => ({
      milestone_id: `0198d6a1-1111-7abc-8000-00000000072${index}`,
      service_case_id: caseId,
      code,
      window_start: "2026-08-29",
      window_end: "2026-08-30",
      status: index < 2 ? "COMPLETED" : "PENDING",
      completed_at: index < 2 ? "2026-08-29T09:00:00Z" : null,
      record_summary: null,
      version: 1,
    })),
    open_high_risk_count: 0,
    closing_readiness: "NOT_READY",
    version: status === "PAUSED" ? 8 : 7,
  };
}

function transfer(status: string) {
  return {
    transfer_id: transferId,
    source_service_case_id: caseId,
    source_tenant_id: "0198d6a1-1111-7abc-8000-000000000713",
    target_tenant_id: "0198d6a1-1111-7abc-8000-000000000714",
    status,
    requested_scope: ["PROFILE", "ASSESSMENT"],
    target_decision: null,
    source_closure_status: null,
    scope_confirmed_at: null,
    transferred_at: null,
    version: 2,
  };
}

function success(data: unknown) {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function failure(status: number, code: string) {
  return new Response(JSON.stringify({ code }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
