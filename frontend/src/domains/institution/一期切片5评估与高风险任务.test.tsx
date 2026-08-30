import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { HealthRecordPage } from "./pages/HealthRecordPage";
import { availableHighRiskActions, HighRiskTaskPage } from "./pages/HighRiskTaskPage";
import { institutionNavigation } from "./navigation";
import { institutionRoutes } from "./routes";

describe("一期切片5机构评估摘要与高风险任务", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    setCurrentUser(null);
  });

  it("注册高风险任务列表详情路由和正式导航", () => {
    expect(institutionRoutes.protectedChildren).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ path: "high-risk-tasks" }),
        expect.objectContaining({ path: "high-risk-tasks/:taskId" }),
      ]),
    );
    expect(institutionNavigation.map((item) => item.label)).toContain("高风险任务");
  });

  it("健康档案展示服务端评估摘要但不展示原始健康数值或内部人员ID", async () => {
    mockHealthRecordBundle();
    renderRoute(
      `/institution/service-cases/${caseId}/health-record`,
      "/institution/service-cases/:caseId/health-record",
      <HealthRecordPage />,
    );

    expect(await screen.findByRole("heading", { name: "健康评估摘要" })).toBeInTheDocument();
    expect(screen.getByText("需重点关注")).toBeInTheDocument();
    expect(screen.getByText("第 2 次评估")).toBeInTheDocument();
    expect(screen.queryByText("138/92")).not.toBeInTheDocument();
    expect(screen.queryByText(/^42$/)).not.toBeInTheDocument();
  });

  it("未知任务状态fail-closed且不产生可写操作", () => {
    expect(availableHighRiskActions("UNKNOWN")).toEqual([]);
  });

  it("org_operator可执行正式任务Action且409后只刷新不自动重放", async () => {
    setCurrentUser({ id: 8, role: USER_ROLES.orgOperator, tenant_id: 2 });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success(task("OPEN", 2)))
      .mockResolvedValueOnce(failure(409, "VERSION_CONFLICT"))
      .mockResolvedValueOnce(success(task("CLAIMED", 3)));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    renderRoute(`/institution/high-risk-tasks/${taskId}`, "/institution/high-risk-tasks/:taskId", <HighRiskTaskPage />);

    await userEvent.click(await screen.findByRole("button", { name: "领取处理" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "处理中" })).toBeInTheDocument());
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("数据已更新");
  });

  it("分配状态只显示已分配或待分配而不暴露内部人员ID", async () => {
    setCurrentUser({ id: 9, role: USER_ROLES.orgAdmin, tenant_id: 2 });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(task("CLAIMED", 2, 42))));
    renderRoute(`/institution/high-risk-tasks/${taskId}`, "/institution/high-risk-tasks/:taskId", <HighRiskTaskPage />);

    expect(await screen.findByText("已分配")).toBeInTheDocument();
    expect(screen.queryByText(/^42$/)).not.toBeInTheDocument();
  });

  it("转介必须由操作人选择联系结果和结构化建议", async () => {
    setCurrentUser({ id: 9, role: USER_ROLES.orgAdmin, tenant_id: 2 });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success(task("OPEN", 2)))
      .mockResolvedValueOnce(success(task("REFERRED", 3)));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    renderRoute(`/institution/high-risk-tasks/${taskId}`, "/institution/high-risk-tasks/:taskId", <HighRiskTaskPage />);

    await userEvent.click(await screen.findByRole("button", { name: "转介医学负责人" }));
    const submit = screen.getByRole("button", { name: "确认提交转介医学负责人" });
    expect(submit).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("联系结果"), "CONTACTED");
    await userEvent.selectOptions(screen.getByLabelText("结构化建议"), "PROMPT_MEDICAL_CONTACT");
    await userEvent.click(submit);

    await screen.findByRole("heading", { name: "已转介" });
    const body = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body));
    expect(body).toEqual(
      expect.objectContaining({
        action_code: "REFER",
        contact_outcome_code: "CONTACTED",
        advice_code: "PROMPT_MEDICAL_CONTACT",
        reason_code: "REFERRED_TO_MEDICAL_RESPONSIBLE_PERSON",
      }),
    );
  });

  it("unknown commit查询后仅以同一Key和同一请求确认", async () => {
    setCurrentUser({ id: 9, role: USER_ROLES.orgAdmin, tenant_id: 2 });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(success(task("OPEN", 2)))
      .mockResolvedValueOnce(failure(503, "COMMIT_OUTCOME_UNKNOWN"))
      .mockResolvedValueOnce(success(task("OPEN", 2)))
      .mockResolvedValueOnce(success(task("CLAIMED", 3)));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    renderRoute(`/institution/high-risk-tasks/${taskId}`, "/institution/high-risk-tasks/:taskId", <HighRiskTaskPage />);

    await userEvent.click(await screen.findByRole("button", { name: "领取处理" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("提交结果暂时无法确认");
    await userEvent.click(screen.getByRole("button", { name: "领取处理" }));
    await screen.findByRole("heading", { name: "处理中" });

    const mutations = fetchMock.mock.calls.filter(([, options]) => options?.method === "POST");
    expect(mutations).toHaveLength(2);
    expect(new Headers(mutations[0]?.[1]?.headers).get("Idempotency-Key")).toBe(
      new Headers(mutations[1]?.[1]?.headers).get("Idempotency-Key"),
    );
    expect(mutations[0]?.[1]?.body).toBe(mutations[1]?.[1]?.body);
  });
});

const caseId = "0198d6a1-1111-7abc-8000-000000000811";
const taskId = "0198d6a1-1111-7abc-8000-000000000812";

function renderRoute(path: string, routePath: string, element: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={element} path={routePath} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockHealthRecordBundle() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/health-record"))
        return Promise.resolve(
          success({
            case_id: caseId,
            profile_completion_status: "COMPLETE",
            missing_section_codes: [],
            indicator_codes: [],
            indicator_states: {},
            report_metadata_count: 0,
            readiness_status: "ASSESSMENT_READY",
            updated_at: "2026-08-30T08:00:00Z",
          }),
        );
      if (url.includes("/detection-reports")) return Promise.resolve(success({ items: [], next_cursor: null }));
      if (url.endsWith("/health-indicators/latest")) return Promise.resolve(success({ items: [] }));
      if (url.endsWith("/assessment-readiness"))
        return Promise.resolve(
          success({
            service_case_id: caseId,
            status: "ASSESSMENT_READY",
            reason_codes: [],
            missing_indicator_codes: [],
            expired_indicator_codes: [],
            disputed_indicator_codes: [],
            profile_revision_id: null,
            policy_version: "ASSESSMENT_INPUT_V1",
            projection_status: "CURRENT",
            data_as_of: "2026-08-30T08:00:00Z",
            generated_at: "2026-08-30T08:00:00Z",
            assembly_id: null,
          }),
        );
      if (url.includes("/assessments"))
        return Promise.resolve(
          success({
            items: [
              {
                assessment_id: "0198d6a1-1111-7abc-8000-000000000813",
                service_case_id: caseId,
                sequence_no: 2,
                status: "COMPLETED",
                overall_risk: "ATTENTION",
                rule_version: "CN_ADULT_BASELINE_V1.1",
                input_snapshot_ref: "0198d6a1-1111-7abc-8000-000000000814",
                supersedes_assessment_id: null,
                initiated_at: "2026-08-30T08:00:00Z",
                completed_at: "2026-08-30T08:01:00Z",
                version: 1,
              },
            ],
            next_cursor: null,
          }),
        );
      throw new Error(`UNEXPECTED_TEST_PATH:${url}`);
    }),
  );
}

function task(status: string, version: number, assignee: number | null = null) {
  return {
    task_id: taskId,
    assessment_id: "0198d6a1-1111-7abc-8000-000000000813",
    service_case_id: caseId,
    status,
    reason_module_codes: ["BLOOD_PRESSURE_CARDIOVASCULAR"],
    assignee,
    due_at: "2026-08-31T08:00:00Z",
    last_action_at: null,
    blocking: { ordinary_plan: true, case_completion: true },
    version,
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
