import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as platformApi from "@/domains/platform/api";
import { PlatformHomePage } from "@/domains/platform/pages/PlatformHomePage";
import { TenantReviewListPage } from "@/domains/platform/pages/TenantReviewListPage";
import { ApiError } from "@/shared/api/errors";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { PlatformShell } from "@/shells/PlatformShell";
import * as institutionApi from "./api";
import { MemberEnrollmentPage } from "./pages/MemberEnrollmentPage";
import { TherapistInvitationPage } from "./pages/TherapistInvitationPage";
import { TherapistListPage } from "./pages/TherapistListPage";
import { ServiceReadinessPage } from "./pages/ServiceReadinessPage";
import type { ReadinessReason, TherapistProfile, TherapistQualification } from "./types";

vi.mock("./api");
vi.mock("@/domains/platform/api");

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.search}</output>;
}

const nearExpiryDate = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

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
  qualification_valid_until: nearExpiryDate,
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
  valid_until: nearExpiryDate,
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

  afterEach(() => {
    setCurrentUser(null);
  });

  it("创建邀请后关闭短码且不把短码放入URL或持久缓存", async () => {
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    render(<TherapistInvitationPage />);
    expect(screen.getByRole("heading", { name: "试点健管师接入" })).toBeInTheDocument();
    expect(screen.getByText("仅用于一期定向试点接入，不代表正式开放注册流程。")).toBeInTheDocument();
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
    expect(screen.getByRole("heading", { name: "健管师团队" })).toBeInTheDocument();
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

  it("SERVICE_READY只展示服务端原因与中文就绪证据且不提供写入口", async () => {
    render(<ServiceReadinessPage />);
    expect(screen.getByText("正在核对服务就绪证据…")).toBeInTheDocument();
    expect(await screen.findByText("服务暂未就绪")).toBeInTheDocument();
    expect(screen.getByText("机构许可证缺失、尚未生效或已过期")).toBeInTheDocument();
    expect(screen.getByText("暂无审核通过且资质有效的健管师")).toBeInTheDocument();
    expect(screen.getByText(/原因：暂无审核通过且资质有效的健管师/)).toBeInTheDocument();
    expect(screen.queryByText(/NO_APPROVED_ACTIVE_THERAPIST/)).not.toBeInTheDocument();
    expect(screen.getByText("就绪证据 v3")).toBeInTheDocument();
    expect(screen.queryByText(/Evidence/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /设为.*就绪/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "就绪证据下一批" }));
    expect(institutionApi.getServiceReadinessEvidence).toHaveBeenLastCalledWith({ cursor: "opaque-c", limit: 20 });
    await userEvent.click(screen.getByRole("button", { name: "就绪证据上一批" }));
    expect(institutionApi.getServiceReadinessEvidence).toHaveBeenLastCalledWith({ limit: 20 });
  });

  it("SERVICE_READY展示服务端权威结论且无原因时给出业务说明", async () => {
    vi.mocked(institutionApi.getServiceReadiness).mockResolvedValueOnce({
      tenant_id: profile.tenant_id,
      readiness_status: "SERVICE_READY",
      reason_codes: [],
      qualified_therapist_count: 2,
      computed_at: "2026-08-17T12:00:00+08:00",
      evidence_version: 4,
    });
    vi.mocked(institutionApi.getServiceReadinessEvidence).mockResolvedValueOnce({ items: [], next_cursor: null });

    render(<ServiceReadinessPage />);

    expect(await screen.findByText("服务已就绪")).toBeInTheDocument();
    expect(screen.getByText("机构许可证、服务范围和合格健管师条件均已满足。")).toBeInTheDocument();
    expect(screen.getByText("暂无历史证据")).toBeInTheDocument();
    expect(screen.queryByText(/Tenant.*已激活/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /设为.*就绪/ })).not.toBeInTheDocument();
  });

  it("未知就绪原因码安全降级且不暴露内部值", async () => {
    const unknownReason = "UNEXPECTED_INTERNAL_REASON" as ReadinessReason;
    vi.mocked(institutionApi.getServiceReadiness).mockResolvedValueOnce({
      tenant_id: profile.tenant_id,
      readiness_status: "NOT_READY",
      reason_codes: [unknownReason],
      qualified_therapist_count: 0,
      computed_at: "2026-08-17T12:00:00+08:00",
      evidence_version: 4,
    });
    vi.mocked(institutionApi.getServiceReadinessEvidence).mockResolvedValueOnce({
      items: [
        {
          tenant_id: profile.tenant_id,
          readiness_status: "NOT_READY",
          reason_codes: [unknownReason],
          qualified_therapist_count: 0,
          computed_at: "2026-08-17T11:00:00+08:00",
          evidence_version: 3,
          input_digest: "a".repeat(64),
          result_digest: "b".repeat(64),
        },
      ],
      next_cursor: null,
    });

    render(<ServiceReadinessPage />);

    expect(await screen.findByText("未识别的就绪条件，请刷新或联系平台核对")).toBeInTheDocument();
    expect(screen.getByText(/原因：/)).toHaveTextContent("原因：未识别的就绪条件，请刷新或联系平台核对");
    expect(screen.queryByText(unknownReason)).not.toBeInTheDocument();
  });

  it.each([
    [401, "登录状态已失效，请重新登录。"],
    [403, "当前账号无权查看该机构的服务资格。"],
    [404, "服务端尚未生成就绪证据，请完成入驻审批后重试。"],
    [503, "服务暂时不可用，当前页面未猜测就绪结果，请稍后重试。"],
  ])("服务就绪读取失败 %s 时显示安全文案并允许刷新恢复", async (status, message) => {
    vi.mocked(institutionApi.getServiceReadiness).mockRejectedValueOnce(
      new ApiError(status, "private backend detail", { code: "INTERNAL_DETAIL" }),
    );

    render(<ServiceReadinessPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText("private backend detail")).not.toBeInTheDocument();
    expect(screen.queryByText("服务已就绪")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "刷新证据" }));
    expect(await screen.findByText("服务暂未就绪")).toBeInTheDocument();
  });

  it("门店审核使用标准submit更新筛选且不写入React控制台错误", async () => {
    vi.mocked(platformApi.listTenantReviewQueue).mockResolvedValue({ items: [], page: 1, page_size: 10, total: 0 });
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/platform/stores/reviews"]}>
          <TenantReviewListPage />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await userEvent.type(screen.getByPlaceholderText("搜索门店名称/编码"), "演示门店");
    await userEvent.click(screen.getByRole("button", { name: "查询" }));

    expect(screen.getByTestId("location")).toHaveTextContent("keyword=%E6%BC%94%E7%A4%BA%E9%97%A8%E5%BA%97");
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("客户展示将平台角色和服务准备状态映射为业务中文", async () => {
    setCurrentUser({ id: 1, role: USER_ROLES.superAdmin, tenant_id: null, org_id: null });
    vi.mocked(platformApi.listTenantReviewQueue).mockResolvedValue({ items: [], page: 1, page_size: 5, total: 0 });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/platform/home"]}>
          <Routes>
            <Route element={<PlatformShell />} path="/platform">
              <Route element={<PlatformHomePage />} path="home" />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getAllByText("平台管理员").length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("超级管理员")).not.toBeInTheDocument();
    expect(screen.queryByText("super_admin")).not.toBeInTheDocument();

    vi.mocked(institutionApi.listMemberEnrollments).mockResolvedValue({ items: [], next_cursor: null });
    render(<MemberEnrollmentPage />, { wrapper: MemoryRouter });
    expect(screen.getByText(/服务接入不代表付费会员关系/)).toBeInTheDocument();
    expect(screen.queryByText(/PREPARING/)).not.toBeInTheDocument();
  });
});
