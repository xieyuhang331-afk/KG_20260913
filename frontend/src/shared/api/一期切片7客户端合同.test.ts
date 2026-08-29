import { afterEach, describe, expect, it, vi } from "vitest";
import {
  acceptServiceTransfer,
  coordinateServiceTransferClose,
  getSafeSlice7Error,
  listInstitutionServiceCases,
  listInstitutionTransfers,
  listPlatformDataExports,
  listPlatformServiceCases,
  pauseInstitutionServiceCase,
  sourceCloseServiceTransfer,
} from "./slice7";

describe("一期切片7客户端合同", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("cursor作为opaque string原样传递且不推导总页数", async () => {
    const fetchMock = stubSuccess({ items: [], next_cursor: null });

    await listInstitutionServiceCases({
      cursor: "signed.cursor.payload",
      limit: 20,
      status: "ACTIVE",
      risk: "AT_RISK",
    });
    await listPlatformServiceCases({ cursor: "another.signed.cursor", limit: 50, status: "PAUSED" });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/institutions/service-cases?cursor=signed.cursor.payload&limit=20&status=ACTIVE&risk=AT_RISK",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/platform/service-fulfillment/cases?cursor=another.signed.cursor&limit=50&status=PAUSED",
    );
  });

  it("机构转机构列表固定使用develop批准的平台路径", async () => {
    const fetchMock = stubSuccess({ items: [], next_cursor: "opaque.next" });

    await listInstitutionTransfers({ status: "ACCEPTED", cursor: "opaque.current", limit: 20 });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/platform/service-transfers?status=ACCEPTED&cursor=opaque.current&limit=20",
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("/institutions/service-transfers");
  });

  it("机构履约状态操作只提交最新version和结构化原因并携带幂等头", async () => {
    const fetchMock = stubSuccess({});

    await pauseInstitutionServiceCase(caseId, { expected_version: 7, reason_code: "INSTITUTION_SERVICE_PAUSED" }, key);

    expectMutation(fetchMock, 0, `/api/v1/institutions/service-cases/${caseId}/pause`, {
      expected_version: 7,
      reason_code: "INSTITUTION_SERVICE_PAUSED",
    });
  });

  it("转机构操作分别使用正式请求DTO且不夹带内部字段", async () => {
    const fetchMock = stubSuccess({});

    await acceptServiceTransfer(
      transferId,
      { expected_version: 3, reason_code: "SERVICE_CONTINUATION_ACCEPTED", service_label: "CONTINUED_HEALTH_SERVICE" },
      key,
    );
    await sourceCloseServiceTransfer(
      transferId,
      { expected_version: 4, summary_id: summaryId, risk_disposition: "HANDOFF_READY" },
      key,
    );
    await coordinateServiceTransferClose(transferId, { expected_version: 5, reason_code: "HANDOFF_READY" }, key);

    expectMutation(fetchMock, 0, `/api/v1/institutions/service-transfers/${transferId}/accept`, {
      expected_version: 3,
      reason_code: "SERVICE_CONTINUATION_ACCEPTED",
      service_label: "CONTINUED_HEALTH_SERVICE",
    });
    expectMutation(fetchMock, 1, `/api/v1/institutions/service-transfers/${transferId}/source-close`, {
      expected_version: 4,
      summary_id: summaryId,
      risk_disposition: "HANDOFF_READY",
    });
    expectMutation(fetchMock, 2, `/api/v1/platform/service-transfers/${transferId}/coordinate-close`, {
      expected_version: 5,
      reason_code: "HANDOFF_READY",
    });
  });

  it("平台导出监督仅使用只读列表合同", async () => {
    const fetchMock = stubSuccess({ items: [], next_cursor: null });
    await listPlatformDataExports({ status: "READY", cursor: "opaque.export", limit: 20 });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/platform/data-exports?status=READY&cursor=opaque.export&limit=20",
    );
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBeUndefined();
  });

  it("409和unknown commit均要求刷新确认且不宣称成功", () => {
    expect(getSafeSlice7Error({ status: 409, payload: { code: "STALE_VERSION" } })).toEqual(
      expect.objectContaining({ refreshRequired: true, resultUnknown: false }),
    );
    expect(getSafeSlice7Error({ status: 503, payload: { code: "COMMIT_OUTCOME_UNKNOWN" } })).toEqual(
      expect.objectContaining({ refreshRequired: true, resultUnknown: true }),
    );
  });
});

const caseId = "0198d6a1-1111-7abc-8000-000000000701";
const transferId = "0198d6a1-1111-7abc-8000-000000000702";
const summaryId = "0198d6a1-1111-7abc-8000-000000000703";
const key = "0198d6a1-1111-7abc-8000-000000000704";

function stubSuccess(data: unknown) {
  const fetchMock = vi
    .fn()
    .mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ data }), { status: 200, headers: { "Content-Type": "application/json" } }),
      ),
    );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function expectMutation(fetchMock: ReturnType<typeof vi.fn>, index: number, path: string, body: object) {
  expect(fetchMock.mock.calls[index]?.[0]).toBe(path);
  expect(fetchMock.mock.calls[index]?.[1]?.method).toBe("POST");
  expect(JSON.parse(String(fetchMock.mock.calls[index]?.[1]?.body))).toEqual(body);
  expect(new Headers(fetchMock.mock.calls[index]?.[1]?.headers).get("Idempotency-Key")).toBe(key);
}
