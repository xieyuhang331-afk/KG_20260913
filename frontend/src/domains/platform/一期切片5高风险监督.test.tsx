import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HighRiskOversightPage } from "./pages/HighRiskOversightPage";
import { allPlatformNavigation } from "./navigation";
import { platformRoutes } from "./routes";

describe("一期切片5平台高风险监督", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("注册只读监督列表和详情路由及导航", () => {
    expect(platformRoutes.protectedChildren.flatMap((route) => route.children ?? [])).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ path: "high-risk-tasks" }),
        expect.objectContaining({ path: "high-risk-tasks/:taskId" }),
      ]),
    );
    expect(allPlatformNavigation.map((item) => item.label)).toContain("高风险监督");
  });

  it("平台详情只读且不显示原始健康数据和内部人员ID", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(task())));
    render(
      <MemoryRouter initialEntries={[`/platform/high-risk-tasks/${taskId}`]}>
        <Routes>
          <Route element={<HighRiskOversightPage />} path="/platform/high-risk-tasks/:taskId" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: /高风险任务监督详情/ })).toBeInTheDocument();
    expect(screen.getByText("已分配")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /领取|升级|转介|解除/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/^42$/)).not.toBeInTheDocument();
    expect(screen.queryByText("138/92")).not.toBeInTheDocument();
  });

  it("503保持依赖不可用语义而不包装成功", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(failure(503, "DEPENDENCY_UNAVAILABLE")));
    render(
      <MemoryRouter initialEntries={["/platform/high-risk-tasks"]}>
        <Routes>
          <Route element={<HighRiskOversightPage />} path="/platform/high-risk-tasks" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂时不可用");
    expect(screen.queryByText("监督记录已完成")).not.toBeInTheDocument();
  });
});

const taskId = "0198d6a1-1111-7abc-8000-000000000821";

function task() {
  return {
    task_id: taskId,
    assessment_id: "0198d6a1-1111-7abc-8000-000000000822",
    service_case_id: "0198d6a1-1111-7abc-8000-000000000823",
    status: "CLAIMED",
    reason_module_codes: ["GLUCOSE_METABOLISM"],
    assignee: 42,
    due_at: "2026-08-31T08:00:00Z",
    last_action_at: "2026-08-30T09:00:00Z",
    blocking: { ordinary_plan: true, case_completion: true },
    version: 2,
    created_at: "2026-08-30T08:00:00Z",
    closed_at: null,
  };
}

function success(data: unknown) {
  return new Response(JSON.stringify({ data }), { status: 200, headers: { "Content-Type": "application/json" } });
}

function failure(status: number, code: string) {
  return new Response(JSON.stringify({ code }), { status, headers: { "Content-Type": "application/json" } });
}
