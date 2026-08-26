import { afterEach, describe, expect, it, vi } from "vitest";
import {
  claimHealthPlanReview,
  createHealthPlanTemplate,
  createPlanGeneration,
  decideHealthPlanReview,
  getPlanGenerationEligibility,
  getSafeSlice6Error,
  listHealthPlanReviews,
  listInstitutionPlans,
  publishHealthPlanTemplate,
  retireHealthPlanTemplate,
} from "./slice6";
import { toUuidV7 } from "./slice3";

describe("一期切片6客户端合同", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("资格查询只读取服务端事实且生成请求仅提交版本", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(success({})));
    vi.stubGlobal("fetch", fetchMock);

    await getPlanGenerationEligibility(caseId);
    await createPlanGeneration(caseId, 7, idempotencyKey);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/institutions/service-cases/${caseId}/plan-generation-eligibility`,
    );
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBeUndefined();
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`/api/v1/institutions/service-cases/${caseId}/plan-generations`);
    expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
      expected_service_case_version: 7,
    });
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get("Idempotency-Key")).toBe(idempotencyKey);
  });

  it("模板发布和退役携带版本与幂等头", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(success({})));
    vi.stubGlobal("fetch", fetchMock);

    await publishHealthPlanTemplate(templateVersionId, 3, idempotencyKey);
    await retireHealthPlanTemplate(templateVersionId, 4, secondIdempotencyKey);

    expectMutation(
      fetchMock,
      0,
      `/api/v1/platform/health-plan-templates/${templateVersionId}/publish`,
      {
        expected_version: 3,
      },
      idempotencyKey,
    );
    expectMutation(
      fetchMock,
      1,
      `/api/v1/platform/health-plan-templates/${templateVersionId}/retire`,
      {
        expected_version: 4,
      },
      secondIdempotencyKey,
    );
  });

  it("模板草稿严格提交正式结构化DTO且机构方案列表不虚构分页参数", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(success({ items: [], next_cursor: null })));
    vi.stubGlobal("fetch", fetchMock);
    const input = {
      template_code: "METABOLIC_FOUNDATION",
      applicable_modules: ["WEIGHT_ABDOMINAL_OBESITY" as const],
      goals_by_module: {
        WEIGHT_ABDOMINAL_OBESITY: ["WEIGHT_GOAL"],
      },
      stage_codes: ["FOUNDATION_STAGE"],
      milestone_codes: ["WEEK_FOUR_REVIEW"],
      sop_codes: ["WEEKLY_FOLLOW_UP"],
      contraindication_codes: ["ACUTE_SYMPTOM_STOP"],
      user_message_codes: ["FOLLOW_APPROVED_PLAN"],
      therapist_action_codes: ["EXPLAIN_APPROVED_PLAN"],
      medical_approval_ref: "MEDICAL-COMMITTEE-2026-01",
    };

    await createHealthPlanTemplate(input, idempotencyKey);
    await listInstitutionPlans(caseId);

    expectMutation(fetchMock, 0, "/api/v1/platform/health-plan-templates", input, idempotencyKey);
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`/api/v1/institutions/service-cases/${caseId}/plans`);
  });

  it("专家审核使用opaque cursor且决定不提交替换正文", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(success({ items: [], next_cursor: null })));
    vi.stubGlobal("fetch", fetchMock);

    await listHealthPlanReviews({ status: "CLAIMED", cursor: "opaque.next.signature", limit: 20 });
    await claimHealthPlanReview(reviewId, 2, idempotencyKey);
    await decideHealthPlanReview(
      reviewId,
      { decision: "NEEDS_CORRECTION", reason_codes: ["TEMPLATE_REAPPLY"], expected_version: 3 },
      secondIdempotencyKey,
    );

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/platform/health-plan-reviews?status=CLAIMED&cursor=opaque.next.signature&limit=20",
    );
    expectMutation(
      fetchMock,
      1,
      `/api/v1/platform/health-plan-reviews/${reviewId}/claim`,
      {
        expected_version: 2,
      },
      idempotencyKey,
    );
    expectMutation(
      fetchMock,
      2,
      `/api/v1/platform/health-plan-reviews/${reviewId}/decision`,
      {
        decision: "NEEDS_CORRECTION",
        reason_codes: ["TEMPLATE_REAPPLY"],
        expected_version: 3,
      },
      secondIdempotencyKey,
    );
    expect(String(fetchMock.mock.calls[2]?.[1]?.body)).not.toMatch(/正文|content|prompt|price/i);
  });

  it("422只显示安全请求合同提示", () => {
    expect(getSafeSlice6Error({ status: 422, payload: { code: "INVALID_REQUEST", detail: "internal" } })).toEqual(
      expect.objectContaining({
        status: 422,
        code: "INVALID_REQUEST",
        message: "请求参数不符合要求，请检查后重试",
      }),
    );
  });
});

const caseId = toUuidV7("0198d6a1-1111-7abc-8000-000000000101");
const templateVersionId = toUuidV7("0198d6a1-1111-7abc-8000-000000000102");
const reviewId = toUuidV7("0198d6a1-1111-7abc-8000-000000000103");
const idempotencyKey = "0198d6a1-1111-7abc-8000-000000000201";
const secondIdempotencyKey = "0198d6a1-1111-7abc-8000-000000000202";

function success(data: unknown) {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function expectMutation(fetchMock: ReturnType<typeof vi.fn>, index: number, path: string, body: object, key: string) {
  expect(fetchMock.mock.calls[index]?.[0]).toBe(path);
  expect(fetchMock.mock.calls[index]?.[1]?.method).toBe("POST");
  expect(JSON.parse(String(fetchMock.mock.calls[index]?.[1]?.body))).toEqual(body);
  expect(new Headers(fetchMock.mock.calls[index]?.[1]?.headers).get("Idempotency-Key")).toBe(key);
}
