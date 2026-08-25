import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HealthPlanPage, PlanEligibilityPanel } from "./pages/HealthPlanPage";
import { institutionRoutes } from "./routes";

describe("一期切片6机构方案生成与状态", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("注册生成进度、方案列表和详情路由", () => {
    expect(institutionRoutes.protectedChildren).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ path: "plan-generations/:requestId" }),
        expect.objectContaining({ path: "service-cases/:caseId/plans" }),
        expect.objectContaining({ path: "plans/:planId" }),
      ]),
    );
  });

  it("资格允许时仅由机构人员受控发起一次确定性生成", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success(eligibility(true)))
      .mockResolvedValueOnce(success(generation("REQUESTED")));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    render(<PlanEligibilityPanel caseId={caseId} />);

    const button = await screen.findByRole("button", { name: "生成健康管理方案" });
    await userEvent.dblClick(button);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/方案生成请求已受理/)).toBeInTheDocument();
    expect(String(fetchMock.mock.calls[1]?.[1]?.body)).toBe(JSON.stringify({ expected_service_case_version: 7 }));
  });

  it("资格不满足时展示服务端原因且不允许提交", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(eligibility(false))));
    render(<PlanEligibilityPanel caseId={caseId} />);

    expect(await screen.findByText("暂不具备方案生成条件")).toBeInTheDocument();
    expect(screen.getByText(/高风险任务尚未解除/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成健康管理方案" })).not.toBeInTheDocument();
  });

  it.each([
    ["REQUESTED", "生成请求已受理"],
    ["GENERATING", "方案生成中"],
    ["GENERATION_FAILED", "方案生成未完成"],
    ["IN_REVIEW", "医学专家审核中"],
    ["NEEDS_CORRECTION", "方案需要补正"],
    ["REJECTED", "方案未通过"],
    ["USER_DECISION_PENDING", "等待用户确认"],
    ["NEEDS_EXPLANATION", "用户需要解释"],
    ["DECLINED", "用户已拒绝"],
    ["ACTIVE", "方案已激活"],
    ["SUPERSEDED", "已有更新版本"],
  ] as const)("将%s显示为安全业务状态", async (status, label) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(generation(status))));
    renderPlanRoute(`/institution/plan-generations/${requestId}`);

    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.queryByText(status)).not.toBeInTheDocument();
  });

  it("未知提交结果只查询原请求且不换Key盲目重提", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success(eligibility(true)))
      .mockResolvedValueOnce(failure(503, "COMMIT_OUTCOME_UNKNOWN"))
      .mockResolvedValueOnce(success(generation("REQUESTED")));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    render(<PlanEligibilityPanel caseId={caseId} />);

    await userEvent.click(await screen.findByRole("button", { name: "生成健康管理方案" }));
    expect(await screen.findByText(/提交结果暂时无法确认/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "查询处理结果" }));
    expect(await screen.findByText("已查询最新处理状态。")).toBeInTheDocument();

    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/plan-generations"))).toHaveLength(2);
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
  });

  it("409刷新生成资格且不自动重放", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success(eligibility(true)))
      .mockResolvedValueOnce(failure(409, "STALE_VERSION"))
      .mockResolvedValueOnce(success(eligibility(false)));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    render(<PlanEligibilityPanel caseId={caseId} />);

    await userEvent.click(await screen.findByRole("button", { name: "生成健康管理方案" }));

    expect(await screen.findByText(/数据已更新/)).toBeInTheDocument();
    expect(screen.getByText("暂不具备方案生成条件")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("plan-generation-eligibility"))).toHaveLength(2);
  });

  it("方案详情只展示获批结构化内容且不允许机构人员编辑医学锁定字段", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(planDetail())));
    renderPlanRoute(`/institution/plans/${planId}`);

    expect(await screen.findByRole("heading", { name: "健康管理方案详情" })).toBeInTheDocument();
    expect(screen.getByText("体重管理目标")).toBeInTheDocument();
    expect(screen.getByText("保持血压管理目标")).toBeInTheDocument();
    expect(screen.queryByText("结构化方案内容")).not.toBeInTheDocument();
    expect(screen.getByText("医学锁定内容仅供查看")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/诊断|处方|药物调整|价格|BOM|AI/)).not.toBeInTheDocument();
  });
});

const caseId = "0198d6a1-1111-7abc-8000-000000000401";
const requestId = "0198d6a1-1111-7abc-8000-000000000402";
const planId = "0198d6a1-1111-7abc-8000-000000000403";

function renderPlanRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/institution/plan-generations/:requestId" element={<HealthPlanPage mode="generation" />} />
        <Route path="/institution/service-cases/:caseId/plans" element={<HealthPlanPage mode="list" />} />
        <Route path="/institution/plans/:planId" element={<HealthPlanPage mode="detail" />} />
      </Routes>
    </MemoryRouter>,
  );
}

function eligibility(eligible: boolean) {
  return {
    service_case_id: caseId,
    eligible,
    blocking_codes: eligible ? [] : ["HIGH_RISK_BLOCKING"],
    current_assessment_id: "0198d6a1-1111-7abc-8000-000000000404",
    assessment_version: 2,
    published_template_version_id: eligible ? "0198d6a1-1111-7abc-8000-000000000405" : null,
    active_generation_request_id: eligible ? requestId : null,
    active_plan_id: null,
    expected_service_case_version: 7,
    evaluated_at: "2026-08-24T08:00:00Z",
  };
}

function generation(status: string) {
  return {
    request_id: requestId,
    service_case_id: caseId,
    status,
    current_plan_id: status === "ACTIVE" ? planId : null,
    current_plan_version: status === "ACTIVE" ? 2 : null,
    failure_code: status === "GENERATION_FAILED" ? "GENERATION_DEPENDENCY_UNAVAILABLE" : null,
    version: 2,
    created_at: "2026-08-24T08:00:00Z",
    updated_at: "2026-08-24T08:10:00Z",
  };
}

function planDetail() {
  return {
    plan_id: planId,
    service_case_id: caseId,
    version_no: 2,
    status: "USER_DECISION_PENDING",
    template_code: "METABOLIC_FOUNDATION",
    template_version: 1,
    overall_risk_level: "ATTENTION",
    created_at: "2026-08-24T08:00:00Z",
    updated_at: "2026-08-24T08:10:00Z",
    version: 5,
    module_summaries: [{ module_code: "WEIGHT_ABDOMINAL_OBESITY", risk_level: "ATTENTION" }],
    goals: ["WEIGHT_GOAL", "GOAL_BP"],
    stages: ["FOUNDATION_STAGE"],
    milestones: ["WEEK_FOUR_REVIEW"],
    sop_items: ["WEEKLY_FOLLOW_UP"],
    contraindication_codes: ["STOP_ON_ACUTE_SYMPTOM"],
    user_message_codes: ["FOLLOW_PLAN_WITH_EXPERT"],
    therapist_action_codes: ["REVIEW_WEEKLY"],
    review_summary: { status: "APPROVED", decision_codes: ["CONTENT_APPROVED"], decided_at: "2026-08-24T08:08:00Z" },
    user_decision_summary: { decision: null, decided_at: null },
    explanations: [],
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
