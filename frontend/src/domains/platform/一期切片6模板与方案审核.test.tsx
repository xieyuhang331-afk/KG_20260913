import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { allPlatformNavigation } from "./navigation";
import { HealthPlanReviewPage } from "./pages/HealthPlanReviewPage";
import { HealthPlanTemplatePage } from "./pages/HealthPlanTemplatePage";
import { platformRoutes } from "./routes";
import { USER_ROLES } from "@/shared/constants/roles";

describe("一期切片6平台模板与方案审核", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("为平台管理员和医学专家注册隔离的导航与路由", () => {
    expect(allPlatformNavigation).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          label: "方案模板治理",
          path: "/platform/health-plan-templates",
          roles: [USER_ROLES.expert, USER_ROLES.sysAdmin, USER_ROLES.superAdmin],
        }),
        expect.objectContaining({
          label: "健康方案审核",
          path: "/platform/health-plan-reviews",
          roles: [USER_ROLES.expert],
        }),
      ]),
    );
    expect(JSON.stringify(platformRoutes.protectedChildren)).toContain("health-plan-templates");
    expect(JSON.stringify(platformRoutes.protectedChildren)).toContain("health-plan-reviews");
  });

  it("模板页面按正式DTO展示结构化锁定字段", async () => {
    mockByPath({
      "/api/v1/platform/health-plan-templates?limit=20": {
        items: [templateSummary()],
        next_cursor: null,
      },
    });
    render(<HealthPlanTemplatePage />, { wrapper: MemoryRouter });

    expect(await screen.findByRole("heading", { name: "方案模板治理" })).toBeInTheDocument();
    expect(screen.getByText("代谢健康基础方案")).toBeInTheDocument();
    expect(screen.getByText("医学锁定字段只读")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建模板草稿" })).toBeEnabled();
    expect(screen.queryByText(/AI|价格|BOM|诊断|处方/)).not.toBeInTheDocument();
  });

  it("专家审核详情只允许结构化决定且不出现生成入口", async () => {
    mockByPath({
      "/api/v1/platform/health-plan-reviews?status=CLAIMED&limit=20": {
        items: [reviewSummary()],
        next_cursor: null,
      },
      [`/api/v1/platform/health-plan-reviews/${reviewId}`]: reviewDetail(),
    });
    render(
      <MemoryRouter initialEntries={[`/platform/health-plan-reviews/${reviewId}`]}>
        <Routes>
          <Route path="/platform/health-plan-reviews/:reviewId" element={<HealthPlanReviewPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "健康方案审核详情" })).toBeInTheDocument();
    expect(screen.getByText("客户健康档案摘要（只读）")).toBeInTheDocument();
    expect(screen.getByText("方案版本差异（只读）")).toBeInTheDocument();
    expect(screen.getByText("代谢健康基础方案")).toBeInTheDocument();
    expect(screen.getAllByText("体重与腹型肥胖：需关注").length).toBeGreaterThan(0);
    expect(screen.getByText("健康目标已更新")).toBeInTheDocument();
    expect(screen.queryByText("METABOLIC_FOUNDATION")).not.toBeInTheDocument();
    expect(screen.queryByText("结构化摘要")).not.toBeInTheDocument();
    expect(screen.getByLabelText("结构化补正原因")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成方案" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /方案正文|医学内容/ })).not.toBeInTheDocument();
  });

  it("409决定冲突刷新详情且不自动重放", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    let detailReads = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = new URL(String(input), "http://local.test").pathname;
      if (path.endsWith(`/health-plan-reviews/${reviewId}/decision`)) {
        return Promise.resolve(failure(409, "REVIEW_DECISION_CONFLICT"));
      }
      if (path.endsWith(`/health-plan-reviews/${reviewId}`)) {
        detailReads += 1;
        return Promise.resolve(success(reviewDetail()));
      }
      throw new Error(`UNEXPECTED_TEST_PATH:${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={[`/platform/health-plan-reviews/${reviewId}`]}>
        <Routes>
          <Route path="/platform/health-plan-reviews/:reviewId" element={<HealthPlanReviewPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "健康方案审核详情" });
    await userEvent.selectOptions(screen.getByLabelText("结构化补正原因"), "TEMPLATE_REAPPLY");
    await userEvent.click(screen.getByRole("button", { name: "要求补正" }));

    expect(await screen.findByText(/数据已更新/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveClass("border-red-200");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/decision"))).toHaveLength(1);
    expect(detailReads).toBe(2);
  });
});

const reviewId = "0198d6a1-1111-7abc-8000-000000000301";

function templateSummary() {
  return {
    template_version_id: "0198d6a1-1111-7abc-8000-000000000302",
    template_code: "METABOLIC_FOUNDATION",
    version_no: 1,
    status: "DRAFT",
    applicable_modules: ["WEIGHT_ABDOMINAL_OBESITY"],
    goals_by_module: { WEIGHT_ABDOMINAL_OBESITY: ["WEIGHT_GOAL"] },
    stage_codes: ["FOUNDATION_STAGE"],
    milestone_codes: ["WEEK_FOUR_REVIEW"],
    sop_codes: ["WEEKLY_FOLLOW_UP"],
    contraindication_codes: ["ACUTE_SYMPTOM_STOP"],
    user_message_codes: ["FOLLOW_APPROVED_PLAN"],
    therapist_action_codes: ["EXPLAIN_APPROVED_PLAN"],
    medical_approval_ref: "MEDICAL-COMMITTEE-2026-01",
    version: 1,
    created_at: "2026-08-24T08:00:00Z",
    published_at: null,
    retired_at: null,
  };
}

function reviewSummary() {
  return {
    review_id: reviewId,
    plan_id: "0198d6a1-1111-7abc-8000-000000000303",
    service_case_id: "0198d6a1-1111-7abc-8000-000000000304",
    request_id: "0198d6a1-1111-7abc-8000-000000000305",
    status: "CLAIMED",
    plan_version_no: 2,
    overall_risk_level: "ATTENTION",
    claimed_at: "2026-08-24T08:05:00Z",
    decided_at: null,
    version: 2,
  };
}

function reviewDetail() {
  return {
    ...reviewSummary(),
    customer_summary_codes: ["ASSESSMENT_INPUT_CURRENT", "PROFILE_CONTEXT_INCLUDED"],
    assessment_summary_codes: ["OVERALL_RISK_ATTENTION", "WEIGHT_ABDOMINAL_OBESITY_ATTENTION"],
    plan_summary: {
      template_code: "METABOLIC_FOUNDATION",
      template_version: 1,
      plan_status: "IN_REVIEW",
      module_summaries: ["WEIGHT_ABDOMINAL_OBESITY_ATTENTION"],
      goals: ["WEIGHT_GOAL"],
      stages: ["FOUNDATION_STAGE"],
      milestones: ["WEEK_FOUR_REVIEW"],
      sop_items: ["WEEKLY_FOLLOW_UP"],
      contraindication_codes: ["ACUTE_SYMPTOM_STOP"],
      user_message_codes: ["FOLLOW_APPROVED_PLAN"],
      therapist_action_codes: ["EXPLAIN_APPROVED_PLAN"],
    },
    version_diff_codes: ["GOALS_CHANGED"],
    history: [{ action: "REVIEW_CLAIMED", plan_version_no: 2, occurred_at: "2026-08-24T08:05:00Z" }],
    reason_codes: [],
    user_decision: null,
  };
}

function mockByPath(values: Record<string, object>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const parsed = new URL(String(input), "http://local.test");
      const key = parsed.pathname + parsed.search;
      const value = values[key];
      if (!value) throw new Error(`UNEXPECTED_TEST_PATH:${key}`);
      return Promise.resolve(success(value));
    }),
  );
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
