import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/shared/api/errors";
import * as institutionApi from "./api";
import { TherapistInvitationPage } from "./pages/TherapistInvitationPage";
import { TherapistListPage } from "./pages/TherapistListPage";
import { ServiceReadinessPage } from "./pages/ServiceReadinessPage";
import type { TherapistProfile, TherapistQualification } from "./types";

vi.mock("./api");

const invitation = {
  invitation_id: "00000000-0000-7000-8000-000000000101",
  masked_phone: "*******0001",
  status: "INVITED" as const,
  expires_at: "2026-08-18T12:00:00+08:00",
  issued_at: "2026-08-17T12:00:00+08:00",
  activated_at: null,
  revoked_at: null,
  version: 1,
};

const profile: TherapistProfile = {
  therapist_id: "00000000-0000-7000-8000-000000000102",
  tenant_id: "00000000-0000-7000-8000-000000000103",
  display_name: "合成健管师甲",
  practice_summary: "代谢健康服务",
  status: "APPROVED_ACTIVE",
  service_tags: ["HYPERTENSION"],
  capacity_limit: 30,
  active_case_count: 3,
  qualification_valid_until: "2026-09-01",
  current_revision_no: 2,
  version: 4,
  updated_at: "2026-08-17T12:00:00+08:00",
};

const qualification: TherapistQualification = {
  qualification_version_id: "00000000-0000-7000-8000-000000000104",
  qualification_type: "METABOLIC_HEALTH_PRACTICE",
  masked_certificate_no: "****0001",
  issuer_name: "合成资质机构",
  valid_from: "2025-09-01",
  valid_until: "2026-09-01",
  derived_review_status: "APPROVED",
  attachment_count: 2,
  version_no: 2,
};

describe("健管师邀请与资格", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(institutionApi.listTherapistInvitations).mockResolvedValue({
      items: [invitation],
      next_cursor: "opaque-a",
    });
    vi.mocked(institutionApi.createTherapistInvitation).mockResolvedValue({ ...invitation, short_code: "000001" });
    vi.mocked(institutionApi.revokeTherapistInvitation).mockResolvedValue({
      ...invitation,
      status: "REVOKED",
      version: 2,
    });
    vi.mocked(institutionApi.listTherapists).mockResolvedValue({ items: [profile], next_cursor: "opaque-b" });
    vi.mocked(institutionApi.getTherapist).mockResolvedValue({ profile, qualifications: [qualification] });
    vi.mocked(institutionApi.getServiceReadiness).mockResolvedValue({
      tenant_id: profile.tenant_id,
      readiness_status: "NOT_READY",
      reason_codes: ["INSTITUTION_LICENSE_INVALID", "NO_APPROVED_ACTIVE_THERAPIST"],
      qualified_therapist_count: 0,
      computed_at: "2026-08-17T12:00:00+08:00",
      evidence_version: 3,
    });
    vi.mocked(institutionApi.getServiceReadinessEvidence).mockResolvedValue({
      items: [
        {
          tenant_id: profile.tenant_id,
          readiness_status: "NOT_READY",
          reason_codes: ["NO_APPROVED_ACTIVE_THERAPIST"],
          qualified_therapist_count: 0,
          computed_at: "2026-08-17T11:00:00+08:00",
          evidence_version: 2,
          input_digest: "a".repeat(64),
          result_digest: "b".repeat(64),
        },
      ],
      next_cursor: "opaque-c",
    });
  });

  it("创建邀请后关闭短码且不把短码放入URL或持久缓存", async () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    render(<TherapistInvitationPage />);
    await screen.findByText(/\*{7}0001/);
    await userEvent.type(screen.getByRole("textbox", { name: "手机号" }), "13800000001");
    await userEvent.click(screen.getByRole("button", { name: "创建邀请" }));
    expect(await screen.findByRole("dialog", { name: "邀请已创建" })).toHaveTextContent("000001");
    expect(window.location.href).not.toContain("000001");
    expect(storageWrite).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "我已安全记录" }));
    expect(screen.queryByText("000001")).not.toBeInTheDocument();
  });

  it("邀请列表使用opaque cursor且撤销必须二次确认", async () => {
    render(<TherapistInvitationPage />);
    await userEvent.click(await screen.findByRole("button", { name: "下一批" }));
    expect(institutionApi.listTherapistInvitations).toHaveBeenLastCalledWith({ cursor: "opaque-a", limit: 20 });
    await userEvent.click(screen.getByRole("button", { name: "撤销邀请" }));
    expect(institutionApi.revokeTherapistInvitation).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    await waitFor(() => expect(institutionApi.revokeTherapistInvitation).toHaveBeenCalledTimes(1));
  });

  it("邀请409只刷新且不自动重放撤销", async () => {
    vi.mocked(institutionApi.revokeTherapistInvitation).mockRejectedValueOnce(
      new ApiError(409, "版本冲突", { code: "THERAPIST_VERSION_CONFLICT" }),
    );
    render(<TherapistInvitationPage />);
    await userEvent.click(await screen.findByRole("button", { name: "撤销邀请" }));
    await userEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    await waitFor(() => expect(institutionApi.listTherapistInvitations).toHaveBeenCalledTimes(2));
    expect(institutionApi.revokeTherapistInvitation).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent("已刷新");
  });

  it("健管师列表展示资质风险负载并按当前tenant读取详情", async () => {
    render(<TherapistListPage />);
    expect(await screen.findByText("合成健管师甲")).toBeInTheDocument();
    expect(screen.getByText("3 / 30")).toBeInTheDocument();
    expect(screen.getByText("即将到期")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "查看合成健管师甲详情" }));
    expect(await screen.findByText("****0001")).toBeInTheDocument();
    expect(screen.getByText("材料 2 份")).toBeInTheDocument();
  });

  it("健管师名单空态说明下一步且503可重试", async () => {
    vi.mocked(institutionApi.listTherapists).mockRejectedValueOnce(
      new ApiError(503, "不可用", { code: "DEPENDENCY_UNAVAILABLE" }),
    );
    render(<TherapistListPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂时不可用");
    vi.mocked(institutionApi.listTherapists).mockResolvedValueOnce({ items: [] });
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("还没有健管师")).toBeInTheDocument();
    expect(screen.getByText(/先创建邀请/)).toBeInTheDocument();
  });

  it("SERVICE_READY只展示服务端原因与Evidence且不提供写入口", async () => {
    render(<ServiceReadinessPage />);
    expect(await screen.findByText("服务暂未就绪")).toBeInTheDocument();
    expect(screen.getByText("机构许可证缺失、尚未生效或已过期")).toBeInTheDocument();
    expect(screen.getByText("暂无审核通过且资质有效的健管师")).toBeInTheDocument();
    expect(screen.getByText("Evidence v3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /设为.*就绪/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Evidence下一批" }));
    expect(institutionApi.getServiceReadinessEvidence).toHaveBeenLastCalledWith({ cursor: "opaque-c", limit: 20 });
  });
});
