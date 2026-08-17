import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/shared/api/errors";
import * as platformApi from "./api";
import { platformNavigation } from "./navigation";
import { TherapistReviewPage } from "./pages/TherapistReviewPage";
import { TherapistStatusPage } from "./pages/TherapistStatusPage";

vi.mock("./api");

const reviewItemId = "00000000-0000-7000-8000-000000000011";
const therapistId = "00000000-0000-7000-8000-000000000012";
const revisionId = "00000000-0000-7000-8000-000000000013";
const currentQualificationId = "00000000-0000-7000-8000-000000000014";
const historicalQualificationId = "00000000-0000-7000-8000-000000000016";

const initialItem = {
  review_item_id: reviewItemId,
  therapist_id: therapistId,
  revision_id: revisionId,
  qualification_version_id: null,
  review_kind: "INITIAL" as const,
  status: "QUEUED" as const,
  created_at: "2026-08-16T00:00:00+08:00",
  claimed_at: null,
  version: 1,
};

const currentQualification = {
  qualification_version_id: currentQualificationId,
  qualification_type: "METABOLIC_HEALTH_PRACTICE" as const,
  masked_certificate_no: "****1234",
  issuer_name: "合成签发机构",
  valid_from: "2026-01-01",
  valid_until: "2027-01-01",
  derived_review_status: "SUBMITTED" as const,
  attachment_count: 1,
  version_no: 2,
};

const historicalQualification = {
  qualification_version_id: historicalQualificationId,
  qualification_type: "METABOLIC_HEALTH_PRACTICE" as const,
  masked_certificate_no: "****5678",
  issuer_name: "历史签发机构",
  valid_from: "2025-01-01",
  valid_until: "2026-01-01",
  derived_review_status: "SUPERSEDED" as const,
  attachment_count: 1,
  version_no: 1,
};

function detailFixture(overrides: Record<string, unknown> = {}) {
  return {
    profile: {
      therapist_id: therapistId,
      tenant_id: "00000000-0000-7000-8000-000000000020",
      display_name: "合成健管师乙",
      practice_summary: "代谢服务摘要",
      status: "RESUBMITTED" as const,
      service_tags: ["OBESITY" as const],
      capacity_limit: 30 as const,
      active_case_count: 0,
      qualification_valid_until: null,
      current_revision_no: 2,
      version: 2,
      updated_at: "2026-08-16T00:00:00+08:00",
    },
    revisions: [
      { revision_id: "00000000-0000-7000-8000-000000000021", revision_no: 1, created_at: "2026-08-15T00:00:00+08:00" },
      { revision_id: revisionId, revision_no: 2, created_at: "2026-08-16T00:00:00+08:00" },
    ],
    qualifications: [currentQualification, historicalQualification],
    current_qualification_ids: [currentQualificationId],
    review_item: initialItem,
    ...overrides,
  };
}

describe("健管师审核", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(platformApi.listTherapistReviews).mockResolvedValue({
      items: [initialItem],
      next_cursor: "opaque-review",
    });
    vi.mocked(platformApi.listTherapistRenewalReviews).mockResolvedValue({ items: [], next_cursor: undefined });
    vi.mocked(platformApi.getTherapistReview).mockResolvedValue(detailFixture());
    vi.mocked(platformApi.decideTherapistReview).mockResolvedValue({
      review_item: { ...initialItem, status: "DECIDED", claimed_at: "2026-08-17T00:00:00+08:00", version: 2 },
      profile: { therapist_id: therapistId, status: "APPROVED_ACTIVE", version: 3 },
      decision_id: "00000000-0000-7000-8000-000000000015",
    });
    vi.mocked(platformApi.decideTherapistRenewalReview).mockResolvedValue({
      review_item: { ...initialItem, status: "DECIDED", claimed_at: "2026-08-17T00:00:00+08:00", version: 2 },
      profile: { therapist_id: therapistId, status: "APPROVED_ACTIVE", version: 3 },
      decision_id: "00000000-0000-7000-8000-000000000015",
    });
    vi.mocked(platformApi.suspendTherapist).mockResolvedValue({
      therapist_id: therapistId,
      status: "SUSPENDED",
      version: 3,
    });
    vi.mocked(platformApi.resumeTherapist).mockResolvedValue({
      therapist_id: therapistId,
      status: "APPROVED_ACTIVE",
      version: 3,
    });
    vi.mocked(platformApi.exitTherapist).mockResolvedValue({ therapist_id: therapistId, status: "EXITED", version: 3 });
  });

  it("平台导航只向super_admin暴露审核与状态页面", () => {
    const items = platformNavigation.filter((item) => item.path.includes("therapist"));
    expect(items).toHaveLength(2);
    expect(items.every((item) => item.roles?.length === 1)).toBe(true);
  });

  it("队列支持初审续期和opaque cursor且详情固定操作栏", async () => {
    render(<TherapistReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "下一批审核" }));
    expect(platformApi.listTherapistReviews).toHaveBeenLastCalledWith({
      kind: "INITIAL",
      cursor: "opaque-review",
      limit: 20,
    });
    await userEvent.click(screen.getByRole("button", { name: "续期审核" }));
    expect(await screen.findByText("暂无续期审核")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "初始审核" }));
    await userEvent.click(await screen.findByRole("button", { name: "查看审核详情" }));
    expect(await screen.findByText("当前 Revision v2")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "审核操作" })).toHaveClass("sticky");
  });

  it("领取审核使用START_REVIEW且操作中禁用决定按钮", async () => {
    render(<TherapistReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看审核详情" }));
    await userEvent.click(await screen.findByRole("button", { name: "领取审核" }));
    await userEvent.click(screen.getByRole("button", { name: "确认领取" }));
    await waitFor(() => expect(platformApi.decideTherapistReview).toHaveBeenCalledTimes(1));
    expect(vi.mocked(platformApi.decideTherapistReview).mock.calls[0]?.[1]).toEqual({
      decision: "START_REVIEW",
      expected_version: 1,
      reason_code: null,
      profile_fields: [],
      qualification_targets: [],
      qualification_outcomes: {},
    });
  });

  it("补正重提后只提交当前Revision资格且历史资格只读展示", async () => {
    const underReview = {
      ...initialItem,
      status: "UNDER_REVIEW" as const,
      version: 2,
      claimed_at: "2026-08-17T00:00:00+08:00",
    };
    vi.mocked(platformApi.listTherapistReviews).mockResolvedValueOnce({ items: [underReview] });
    vi.mocked(platformApi.getTherapistReview).mockResolvedValueOnce(detailFixture({ review_item: underReview }));
    render(<TherapistReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看审核详情" }));
    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    await userEvent.click(screen.getByRole("button", { name: "确认批准" }));
    await waitFor(() => expect(platformApi.decideTherapistReview).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(platformApi.decideTherapistReview).mock.calls[0]?.[1];
    expect(payload?.qualification_outcomes).toEqual({ [currentQualificationId]: "APPROVED" });
    expect(payload?.qualification_outcomes).not.toHaveProperty(revisionId);
    expect(payload?.qualification_outcomes).not.toHaveProperty(historicalQualificationId);
    expect(screen.getByText("历史版本")).toBeInTheDocument();
  });

  it("续期审核调用review_item路由且只提交绑定资格", async () => {
    const renewalItem = {
      ...initialItem,
      review_kind: "RENEWAL" as const,
      status: "UNDER_REVIEW" as const,
      qualification_version_id: currentQualificationId,
      claimed_at: "2026-08-17T00:00:00+08:00",
    };
    vi.mocked(platformApi.listTherapistRenewalReviews).mockResolvedValueOnce({ items: [renewalItem] });
    vi.mocked(platformApi.getTherapistReview).mockResolvedValueOnce(detailFixture({ review_item: renewalItem }));
    render(<TherapistReviewPage />);
    await userEvent.click(screen.getByRole("button", { name: "续期审核" }));
    await userEvent.click(await screen.findByRole("button", { name: "查看审核详情" }));
    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    await userEvent.click(screen.getByRole("button", { name: "确认批准" }));
    await waitFor(() => expect(platformApi.decideTherapistRenewalReview).toHaveBeenCalledTimes(1));
    expect(vi.mocked(platformApi.decideTherapistRenewalReview).mock.calls[0]?.[0]).toBe(reviewItemId);
    expect(vi.mocked(platformApi.decideTherapistRenewalReview).mock.calls[0]?.[1].qualification_outcomes).toEqual({
      [currentQualificationId]: "APPROVED",
    });
  });

  it.each([
    ["空当前集合", []],
    ["重复当前ID", [currentQualificationId, currentQualificationId]],
  ])("%s固定失败且不调用决定mutation", async (_label, currentIds) => {
    const underReview = {
      ...initialItem,
      status: "UNDER_REVIEW" as const,
      version: 2,
      claimed_at: "2026-08-17T00:00:00+08:00",
    };
    vi.mocked(platformApi.getTherapistReview).mockResolvedValueOnce(
      detailFixture({ current_qualification_ids: currentIds, review_item: underReview }),
    );
    render(<TherapistReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看审核详情" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("审核资质集合已变化");
    expect(screen.queryByRole("button", { name: "批准" })).not.toBeInTheDocument();
    expect(platformApi.decideTherapistReview).not.toHaveBeenCalled();
  });

  it("409后只刷新且不自动重放审核决定", async () => {
    const underReview = {
      ...initialItem,
      status: "UNDER_REVIEW" as const,
      version: 2,
      claimed_at: "2026-08-17T00:00:00+08:00",
    };
    vi.mocked(platformApi.listTherapistReviews).mockResolvedValue({ items: [underReview] });
    vi.mocked(platformApi.getTherapistReview).mockResolvedValue(detailFixture({ review_item: underReview }));
    vi.mocked(platformApi.decideTherapistReview).mockRejectedValueOnce(
      new ApiError(409, "conflict", { code: "THERAPIST_VERSION_CONFLICT" }),
    );
    render(<TherapistReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看审核详情" }));
    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    await userEvent.click(screen.getByRole("button", { name: "确认批准" }));
    await waitFor(() => expect(platformApi.listTherapistReviews).toHaveBeenCalledTimes(2));
    expect(platformApi.decideTherapistReview).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent("已刷新");
  });

  it("暂停恢复退出来自真实详情并均要求二次确认", async () => {
    vi.mocked(platformApi.getTherapistReview).mockResolvedValue(
      detailFixture({ profile: { ...detailFixture().profile, status: "APPROVED_ACTIVE" as const } }),
    );
    render(<TherapistStatusPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看状态详情" }));
    await userEvent.click(await screen.findByRole("button", { name: "暂停服务" }));
    expect(platformApi.suspendTherapist).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认暂停" }));
    await waitFor(() =>
      expect(platformApi.suspendTherapist).toHaveBeenCalledWith(
        therapistId,
        2,
        "COMPLIANCE_SUSPENDED",
        expect.any(String),
      ),
    );
  });

  it("状态操作503提示结果未知且不自动重放", async () => {
    vi.mocked(platformApi.getTherapistReview).mockResolvedValue(
      detailFixture({ profile: { ...detailFixture().profile, status: "APPROVED_ACTIVE" as const } }),
    );
    vi.mocked(platformApi.suspendTherapist).mockRejectedValueOnce(
      new ApiError(503, "unknown", { code: "COMMIT_OUTCOME_UNKNOWN" }),
    );
    render(<TherapistStatusPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看状态详情" }));
    await userEvent.click(await screen.findByRole("button", { name: "暂停服务" }));
    await userEvent.click(screen.getByRole("button", { name: "确认暂停" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("结果暂时无法确认");
    expect(platformApi.suspendTherapist).toHaveBeenCalledTimes(1);
  });
});
