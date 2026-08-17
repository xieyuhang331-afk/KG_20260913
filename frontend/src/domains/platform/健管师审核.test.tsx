import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/shared/api/errors";
import { platformNavigation } from "./navigation";
import * as platformApi from "./api";
import { TherapistReviewPage } from "./pages/TherapistReviewPage";

vi.mock("./api");

const reviewItemId = "00000000-0000-7000-8000-000000000011";
const therapistId = "00000000-0000-7000-8000-000000000012";
const revisionId = "00000000-0000-7000-8000-000000000013";
const currentQualificationId = "00000000-0000-7000-8000-000000000014";
const historicalQualificationId = "00000000-0000-7000-8000-000000000016";

const currentQualification = {
  qualification_version_id: currentQualificationId,
  qualification_type: "METABOLIC_HEALTH_PRACTICE" as const,
  masked_certificate_no: "****1234",
  issuer_name: "current issuer",
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
  issuer_name: "historical issuer",
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
      display_name: "合同名称",
      practice_summary: "摘要",
      status: "RESUBMITTED" as const,
      service_tags: ["OBESITY" as const],
      capacity_limit: 30 as const,
      active_case_count: 0,
      qualification_valid_until: null,
      current_revision_no: 2,
      version: 2,
      updated_at: "2026-08-16T00:00:00+08:00",
    },
    qualifications: [currentQualification, historicalQualification],
    current_qualification_ids: [currentQualificationId],
    review_item: {
      review_item_id: reviewItemId,
      therapist_id: therapistId,
      revision_id: revisionId,
      qualification_version_id: null,
      review_kind: "INITIAL" as const,
      status: "QUEUED" as const,
      created_at: "2026-08-16T00:00:00+08:00",
      version: 1,
    },
    ...overrides,
  };
}

describe("健管师审核", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(platformApi.listTherapistReviews).mockResolvedValue({
      items: [detailFixture().review_item],
    });
    vi.mocked(platformApi.getTherapistReview).mockResolvedValue(detailFixture());
    vi.mocked(platformApi.decideTherapistReview).mockResolvedValue({
      review_item: { review_item_id: reviewItemId, status: "DECIDED", version: 2 },
      profile: { therapist_id: therapistId, status: "APPROVED_ACTIVE", version: 3 },
      decision_id: "00000000-0000-7000-8000-000000000015",
    });
  });

  it("平台导航只向super_admin暴露审核与状态页面", () => {
    const items = platformNavigation.filter((item) => item.path.includes("therapist"));
    expect(items).toHaveLength(2);
    expect(items.every((item) => item.roles?.length === 1)).toBe(true);
  });

  it("补正重提后只提交当前Revision资格且历史资格只读展示", async () => {
    render(<TherapistReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    await waitFor(() => expect(platformApi.decideTherapistReview).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(platformApi.decideTherapistReview).mock.calls[0]?.[1];
    expect(payload?.qualification_outcomes).toEqual({ [currentQualificationId]: "APPROVED" });
    expect(payload?.qualification_outcomes).not.toHaveProperty(revisionId);
    expect(payload?.qualification_outcomes).not.toHaveProperty(historicalQualificationId);
    expect(await screen.findByText(/历史资质.*historical issuer/)).toBeInTheDocument();
  });

  it("续期审核只提交review_item绑定的当前资格", async () => {
    const detail = detailFixture({
      review_item: {
        ...detailFixture().review_item,
        review_kind: "RENEWAL",
        qualification_version_id: currentQualificationId,
      },
    });
    vi.mocked(platformApi.listTherapistReviews).mockResolvedValueOnce({ items: [detail.review_item] });
    vi.mocked(platformApi.getTherapistReview).mockResolvedValueOnce(detail);
    render(<TherapistReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    await waitFor(() => expect(platformApi.decideTherapistReview).toHaveBeenCalledTimes(1));
    expect(vi.mocked(platformApi.decideTherapistReview).mock.calls[0]?.[1].qualification_outcomes).toEqual({
      [currentQualificationId]: "APPROVED",
    });
  });

  it.each([
    ["空当前集合", []],
    ["重复当前ID", [currentQualificationId, currentQualificationId]],
  ])("%s固定失败且不调用决定mutation", async (_label, currentIds) => {
    vi.mocked(platformApi.getTherapistReview).mockResolvedValueOnce(
      detailFixture({ current_qualification_ids: currentIds }),
    );
    render(<TherapistReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("审核资质集合已变化"));
    expect(platformApi.decideTherapistReview).not.toHaveBeenCalled();
  });

  it("409后只刷新且不自动重放审核决定", async () => {
    vi.mocked(platformApi.decideTherapistReview).mockRejectedValueOnce(
      new ApiError(409, "conflict", { code: "THERAPIST_VERSION_CONFLICT" }),
    );
    render(<TherapistReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "批准" }));
    await waitFor(() => expect(platformApi.listTherapistReviews).toHaveBeenCalledTimes(2));
    expect(platformApi.decideTherapistReview).toHaveBeenCalledTimes(1);
  });
});
