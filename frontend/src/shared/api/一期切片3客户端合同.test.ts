import { afterEach, describe, expect, it, vi } from "vitest";
import {
  assignPrimaryTherapist,
  cancelPrimaryAssignment,
  checkMemberIdentity,
  createMemberInvitation,
  listMemberInvitations,
  resendMemberInvitation,
  revokeMemberInvitation,
} from "@/domains/institution/api";
import {
  accessMemberIdentityPii,
  createConsentDocument,
  decideMemberIdentityReview,
  listMemberIdentityReviews,
  publishConsentDocument,
  retireConsentDocument,
} from "@/domains/platform/实名认证审核接口";
import { getSafeApiError, isUuidV7, toUuidV7 } from "./slice3";

const reviewId = toUuidV7("0198b963-38f0-7d7d-8000-000000000021");
const revisionId = toUuidV7("0198b963-38f0-7d7d-8000-000000000022");
const relatedId = toUuidV7("0198b963-38f0-7d7d-8000-000000000023");
const syntheticFullId = ["11010519491231002", "X"].join("");

describe("一期切片3客户端精确合同", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("只接受 UUIDv7 公共 ID", () => {
    expect(isUuidV7(reviewId)).toBe(true);
    expect(isUuidV7("550e8400-e29b-41d4-a716-446655440000")).toBe(false);
  });

  it("会员邀请使用 cursor、Idempotency-Key 和精确版本载荷", async () => {
    const fetchMock = mockJson({ items: [], next_cursor: "opaque-next" });
    await listMemberInvitations({ cursor: "opaque-current", limit: 25, status: "INVITED" });
    expect(fetchMock.mock.calls[0]?.[0]).toContain(
      "/api/v1/institution/member-invitations?cursor=opaque-current&limit=25&status=INVITED",
    );

    fetchMock.mockResolvedValueOnce(jsonResponse(invitationSecret()));
    await createMemberInvitation({ mode: "SELF", phone: "13800000000" }, "idem-create");
    expectRequest(fetchMock, 1, "/api/v1/institution/member-invitations", "idem-create", {
      mode: "SELF",
      phone: "13800000000",
    });

    fetchMock.mockResolvedValueOnce(jsonResponse({ ...invitationSecret(), status: "REVOKED", short_code: undefined }));
    await revokeMemberInvitation(reviewId, 3, "INSTITUTION_CANCELLED", "idem-revoke");
    expectRequest(fetchMock, 2, `/api/v1/institution/member-invitations/${reviewId}/revoke`, "idem-revoke", {
      expected_version: 3,
      reason_code: "INSTITUTION_CANCELLED",
    });
  });

  it("平台实名仅调用 UUIDv7 Slice3 路径且 PII 请求 no-store", async () => {
    const fetchMock = mockJson({ items: [], next_cursor: null });
    await listMemberIdentityReviews({ status: "INSTITUTION_CHECKED", limit: 20 });
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/api/v1/platform/member-identity-reviews?");
    expect(fetchMock.mock.calls[0]?.[0]).not.toContain("/api/v1/reviews/");

    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        review_id: reviewId,
        revision_id: revisionId,
        real_name: "合成用户",
        id_number: syntheticFullId,
        birth_date: "1949-12-31",
        access_id: "0198b963-38f0-7d7d-8000-000000000023",
      }),
    );
    await accessMemberIdentityPii(reviewId, "ephemeral-password", "PLATFORM_IDENTITY_REVIEW", "idem-pii");
    const piiRequest = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(piiRequest.cache).toBe("no-store");
    expect(String(fetchMock.mock.calls[1]?.[0])).not.toContain("ephemeral-password");

    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "APPROVED" }));
    await decideMemberIdentityReview(
      reviewId,
      {
        revision_id: revisionId,
        decision: "APPROVED",
        reason_code: null,
        correction_fields: [],
        represented_elder_eligible: null,
        expected_version: 4,
      },
      "idem-decision",
    );
    expectRequest(fetchMock, 2, `/api/v1/platform/member-identity-reviews/${reviewId}/decision`, "idem-decision", {
      revision_id: revisionId,
      decision: "APPROVED",
      reason_code: null,
      correction_fields: [],
      represented_elder_eligible: null,
      expected_version: 4,
    });
  });

  it("机构实名与主健管师 mutation 精确携带幂等键和当前版本", async () => {
    const fetchMock = mockJson({ status: "CHECKED" });
    await checkMemberIdentity(
      reviewId,
      {
        revision_id: revisionId,
        decision: "CHECKED",
        reason_code: null,
        correction_fields: [],
        attestation_code: "OFFLINE_IDENTITY_CHECKED",
        expected_version: 7,
      },
      "idem-check",
    );
    expectRequest(fetchMock, 0, `/api/v1/institution/member-enrollments/${reviewId}/identity-check`, "idem-check", {
      revision_id: revisionId,
      decision: "CHECKED",
      reason_code: null,
      correction_fields: [],
      attestation_code: "OFFLINE_IDENTITY_CHECKED",
      expected_version: 7,
    });

    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "PENDING_ACCEPTANCE" }));
    await assignPrimaryTherapist(
      reviewId,
      { therapist_id: relatedId, service_scope_tags: ["HYPERTENSION"], expected_version: 8 },
      "idem-assign",
    );
    expectRequest(
      fetchMock,
      1,
      `/api/v1/institution/member-enrollments/${reviewId}/primary-assignments`,
      "idem-assign",
      { therapist_id: relatedId, service_scope_tags: ["HYPERTENSION"], expected_version: 8 },
    );

    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "CANCELLED" }));
    await cancelPrimaryAssignment(relatedId, 3, "INSTITUTION_CANCELLED", "idem-cancel");
    expectRequest(fetchMock, 2, `/api/v1/institution/primary-assignments/${relatedId}/cancel`, "idem-cancel", {
      expected_version: 3,
      reason_code: "INSTITUTION_CANCELLED",
    });
  });

  it("重发邀请与同意文档状态变更使用固定合同", async () => {
    const fetchMock = mockJson(invitationSecret());
    await resendMemberInvitation(reviewId, 4, "idem-resend");
    expectRequest(fetchMock, 0, `/api/v1/institution/member-invitations/${reviewId}/resend`, "idem-resend", {
      expected_version: 4,
    });

    fetchMock.mockResolvedValueOnce(jsonResponse({ document_version_id: relatedId, version: 1 }));
    await createConsentDocument(
      {
        document_type: "USER_AGREEMENT",
        semantic_version: "1.0.0",
        requires_reconsent: true,
        renditions: [{ locale: "zh-CN", title: "合成协议", body: "仅用于合同测试。" }],
      },
      "idem-consent-create",
    );
    expectRequest(fetchMock, 1, "/api/v1/platform/consent-documents", "idem-consent-create", {
      document_type: "USER_AGREEMENT",
      semantic_version: "1.0.0",
      requires_reconsent: true,
      renditions: [{ locale: "zh-CN", title: "合成协议", body: "仅用于合同测试。" }],
    });

    fetchMock.mockResolvedValueOnce(jsonResponse({ document_version_id: relatedId, version: 2 }));
    await publishConsentDocument(relatedId, 1, "2026-08-22T00:00:00+08:00", "idem-publish");
    expectRequest(fetchMock, 2, `/api/v1/platform/consent-documents/${relatedId}/publish`, "idem-publish", {
      expected_version: 1,
      effective_at: "2026-08-22T00:00:00+08:00",
    });

    fetchMock.mockResolvedValueOnce(jsonResponse({ document_version_id: relatedId, version: 3 }));
    await retireConsentDocument(relatedId, 2, "SUPERSEDED_BY_NEW_VERSION", "idem-retire");
    expectRequest(fetchMock, 3, `/api/v1/platform/consent-documents/${relatedId}/retire`, "idem-retire", {
      expected_version: 2,
      reason_code: "SUPERSEDED_BY_NEW_VERSION",
    });
  });

  it.each([
    [400, "INVALID_REQUEST", "请求内容不符合要求"],
    [401, "SESSION_EXPIRED", "登录状态已失效"],
    [403, "TENANT_SCOPE_FORBIDDEN", "当前账号无权执行此操作"],
    [404, "ENROLLMENT_NOT_FOUND", "未找到该记录"],
    [409, "VERSION_CONFLICT", "数据已更新，请刷新后重试"],
    [429, "STEP_UP_RATE_LIMITED", "操作过于频繁，请稍后重试"],
    [503, "COMMIT_OUTCOME_UNKNOWN", "提交结果暂时无法确认"],
  ])("将 %s/%s 映射为固定安全文案", (status, code, message) => {
    expect(getSafeApiError({ status, payload: { code } }).message).toContain(message);
  });
});

function mockJson(data: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(data));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function jsonResponse(data: unknown) {
  return new Response(JSON.stringify({ data, request_id: reviewId }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function expectRequest(
  fetchMock: ReturnType<typeof vi.fn>,
  callIndex: number,
  path: string,
  idempotencyKey: string,
  body: object,
) {
  const [url, options] = fetchMock.mock.calls[callIndex] as [string, RequestInit];
  expect(url).toContain(path);
  expect(new Headers(options.headers).get("Idempotency-Key")).toBe(idempotencyKey);
  expect(JSON.parse(String(options.body))).toEqual(body);
}

function invitationSecret() {
  return {
    invitation_id: reviewId,
    tenant_id: revisionId,
    mode: "SELF",
    phone_masked: "138****0000",
    expires_at: "2026-08-22T00:00:00+08:00",
    status: "INVITED",
    failed_attempts: 0,
    issued_at: "2026-08-21T00:00:00+08:00",
    accepted_at: null,
    revoked_at: null,
    version: 1,
    short_code: "123456",
  };
}
