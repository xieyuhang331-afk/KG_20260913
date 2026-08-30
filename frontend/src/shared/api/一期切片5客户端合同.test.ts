import { afterEach, describe, expect, it, vi } from "vitest";
import {
  actInstitutionHighRiskTask,
  getSafeSlice5Error,
  listInstitutionAssessments,
  listInstitutionHighRiskTasks,
  listPlatformHighRiskTasks,
} from "./slice5";

describe("一期切片5客户端合同", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("评估摘要使用正式单数institution路径且cursor保持opaque", async () => {
    const fetchMock = stubSuccess({ items: [], next_cursor: null });

    await listInstitutionAssessments(caseId, { cursor: "signed.assessment.cursor", limit: 20 });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/institution/service-cases/${caseId}/assessments?cursor=signed.assessment.cursor&limit=20`,
    );
  });

  it("机构高风险任务只使用正式单数institution路径", async () => {
    const fetchMock = stubSuccess({ items: [], next_cursor: "opaque.next" });

    await listInstitutionHighRiskTasks({ status: "CLAIMED", cursor: "opaque.current", limit: 20 });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/institution/high-risk-tasks?status=CLAIMED&cursor=opaque.current&limit=20",
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("/institutions/");
  });

  it("机构任务Action提交最新version、正式结构化码和稳定幂等头", async () => {
    const fetchMock = stubSuccess({});
    const input = {
      expected_version: 7,
      action_code: "REFER" as const,
      contact_outcome_code: "CONTACTED" as const,
      advice_code: "PROMPT_MEDICAL_CONTACT" as const,
      reason_code: "REFERRED_TO_MEDICAL_RESPONSIBLE_PERSON" as const,
      occurred_at: "2026-08-30T10:00:00+08:00",
    };

    await actInstitutionHighRiskTask(taskId, input, key);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(`/api/v1/institution/high-risk-tasks/${taskId}/actions`);
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(input);
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("Idempotency-Key")).toBe(key);
  });

  it("平台高风险任务保持只读监督合同", async () => {
    const fetchMock = stubSuccess({ items: [], next_cursor: null });

    await listPlatformHighRiskTasks({ status: "OPEN", cursor: "opaque.platform", limit: 50 });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/platform/high-risk-tasks?status=OPEN&cursor=opaque.platform&limit=50",
    );
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBeUndefined();
  });

  it("409和unknown commit均要求查询权威状态而不宣称成功", () => {
    expect(getSafeSlice5Error({ status: 409, payload: { code: "VERSION_CONFLICT" } })).toEqual(
      expect.objectContaining({ refreshRequired: true, resultUnknown: false }),
    );
    expect(getSafeSlice5Error({ status: 503, payload: { code: "COMMIT_OUTCOME_UNKNOWN" } })).toEqual(
      expect.objectContaining({ refreshRequired: true, resultUnknown: true }),
    );
  });

  it.each([
    [401, "登录状态已失效"],
    [403, "当前账号无权"],
    [404, "未找到该记录"],
    [422, "请求参数或分页凭据"],
    [503, "服务暂时不可用"],
  ])("HTTP %s使用安全中文错误且不透传后端detail", (status, message) => {
    expect(getSafeSlice5Error({ status, payload: { code: "SAFE_CODE", detail: "database secret" } }).message).toContain(
      message,
    );
    expect(
      getSafeSlice5Error({ status, payload: { code: "SAFE_CODE", detail: "database secret" } }).message,
    ).not.toContain("database secret");
  });
});

const caseId = "0198d6a1-1111-7abc-8000-000000000801";
const taskId = "0198d6a1-1111-7abc-8000-000000000802";
const key = "0198d6a1-1111-7abc-8000-000000000803";

function stubSuccess(data: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ data }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
