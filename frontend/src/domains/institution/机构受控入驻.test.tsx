import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/shared/api/errors";
import * as platformApi from "../platform/api";
import * as organizationApi from "@/domains/organization/组织基础接口";
import platformApiSource from "../platform/api.ts?raw";
import { InstitutionReviewPage } from "../platform/pages/InstitutionReviewPage";
import institutionReviewPageSource from "../platform/pages/InstitutionReviewPage.tsx?raw";
import { platformRoutes } from "../platform/routes";
import * as institutionApi from "./api";
import type { OnboardingApplication } from "./api";
import institutionApiSource from "./api.ts?raw";
import { ControlledOnboardingPage } from "./pages/ControlledOnboardingPage";
import { InstitutionActivationPage } from "./pages/InstitutionActivationPage";
import controlledOnboardingPageSource from "./pages/ControlledOnboardingPage.tsx?raw";
import { InstitutionInvitationPage } from "../platform/pages/InstitutionInvitationPage";
import { institutionRoutes } from "./routes";
import { ConfirmDialog, onboardingErrorMessage } from "./受控入驻界面";
import { platformNavigation } from "../platform/navigation";
import { PlatformShell } from "@/shells/PlatformShell";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";

vi.mock("./api");
vi.mock("../platform/api");
vi.mock("@/domains/organization/组织基础接口");

const draft: OnboardingApplication = {
  application_id: "application-1",
  institution_type: "HEALTH_STORE",
  status: "DRAFT",
  draft: {},
  correction_fields: [],
  version: 1,
  tenant_active: false,
  service_ready: false,
  licenses: [],
};

beforeEach(() => {
  vi.resetAllMocks();
  setCurrentUser(null);
  vi.mocked(organizationApi.listOrganizationTree).mockResolvedValue([
    {
      organization_id: 1001,
      parent_id: 1000,
      org_code: "TEST-COUNTY",
      org_name: "测试区县",
      org_type: "county",
      status: "active",
      compatibility_mode: "canonical",
      sort_order: 1,
      version: 1,
      children: [],
    },
  ]);
});

describe("一期切片1机构受控入驻", () => {
  it("一期入驻生产前端不包含敏感值、持久Token或原始日志", () => {
    const sources = [
      institutionApiSource,
      controlledOnboardingPageSource,
      platformApiSource,
      institutionReviewPageSource,
    ];
    const forbidden = [
      /(?<!\d)1[3-9]\d{9}(?!\d)/,
      /(?<!\d)\d{17}[0-9Xx](?!\w)/,
      /\bBearer\s+[A-Za-z0-9._~-]+/,
      /\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/,
      /\bconsole\.(?:debug|info|log|warn|error)\s*\(/,
    ];
    for (const source of sources) {
      for (const pattern of forbidden) expect(source).not.toMatch(pattern);
    }
  });

  it("机构端与平台端路由使用受控合同", () => {
    const indexRoute = institutionRoutes.protectedChildren.find((route) => route.index);
    expect(indexRoute?.element).toMatchObject({ props: { to: "/institution/store/application" } });
    expect(institutionRoutes.protectedChildren.some((route) => route.path === "store/application")).toBe(true);
    const protectedRoutes = platformRoutes.protectedChildren.flatMap((route) => route.children ?? []);
    expect(protectedRoutes.some((route) => route.path === "institution-invitations")).toBe(true);
    expect(protectedRoutes.some((route) => route.path === "institution-reviews")).toBe(true);
  });

  it("平台全部导航项都渲染实际图标", () => {
    setCurrentUser({ id: 1, role: USER_ROLES.superAdmin });
    render(
      <MemoryRouter initialEntries={["/platform/home"]}>
        <Routes>
          <Route path="/platform" element={<PlatformShell />}>
            <Route path="home" element={<div>平台首页</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    const desktopNavigation = screen.getByRole("navigation", { name: "平台治理主导航" });
    for (const item of platformNavigation) {
      const link = within(desktopNavigation).getByRole("link", { name: item.label });
      expect(link.querySelector("svg"), item.label).not.toBeNull();
    }
  });

  it("机构激活将邀请短码与TOTP作为显式受控请求", async () => {
    vi.mocked(institutionApi.activateInstitution).mockResolvedValue({
      application_id: "application-1",
      status: "DRAFT",
    });
    render(<InstitutionActivationPage />);
    for (const [label, value] of [
      ["邀请 ID", "0198a58f-4900-7000-8000-000000000001"],
      ["冻结手机号", ["139", "0000", "0000"].join("")],
      ["6 位短码", "000001"],
      ["密码", "StrongPassword123!"],
      ["TOTP 密钥", "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"],
      ["当前 TOTP", "000002"],
    ] as const)
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    await userEvent.click(screen.getByRole("button", { name: "确认激活" }));
    await waitFor(() =>
      expect(institutionApi.activateInstitution).toHaveBeenCalledWith(
        expect.objectContaining({
          short_code: "000001",
          totp_code: "000002",
        }),
        expect.any(String),
      ),
    );
  });

  it("机构激活失败后立即清空并遮罩短码、密码与TOTP凭据", async () => {
    vi.mocked(institutionApi.activateInstitution).mockRejectedValue(
      new ApiError(401, "ONBOARDING_ACTIVATION_REJECTED", null),
    );
    render(<InstitutionActivationPage />);
    for (const [label, value] of [
      ["邀请 ID", "0198a58f-4900-7000-8000-000000000001"],
      ["冻结手机号", ["139", "0000", "0000"].join("")],
      ["6 位短码", "000001"],
      ["密码", "StrongPassword123!"],
      ["TOTP 密钥", "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"],
      ["当前 TOTP", "000002"],
    ] as const)
      fireEvent.change(screen.getByLabelText(label), { target: { value } });

    expect(screen.getByLabelText("TOTP 密钥")).toHaveAttribute("type", "password");
    await userEvent.click(screen.getByRole("button", { name: "确认激活" }));
    await screen.findByRole("alert");
    for (const label of ["6 位短码", "密码", "TOTP 密钥", "当前 TOTP"])
      expect(screen.getByLabelText(label)).toHaveValue("");
  });

  it("平台邀请显式提交机构类型与行政区县", async () => {
    vi.mocked(platformApi.listInstitutionInvitations).mockResolvedValue([]);
    vi.mocked(platformApi.createInstitutionInvitation).mockResolvedValue({
      invitation_id: "invitation-1",
      institution_name: "一期健康门店",
      institution_type: "HEALTH_STORE",
      status: "ISSUED",
      version: 1,
      short_code: "000001",
    });
    render(<InstitutionInvitationPage />);
    fireEvent.change(screen.getByPlaceholderText("机构名称"), {
      target: { value: "一期健康门店" },
    });
    fireEvent.change(screen.getByPlaceholderText("申请人手机号"), {
      target: { value: ["139", "0000", "0000"].join("") },
    });
    fireEvent.change(screen.getByPlaceholderText("试点批次"), {
      target: { value: "PILOT-01" },
    });
    await userEvent.selectOptions(await screen.findByLabelText("归属区/县"), "1001");
    await userEvent.click(screen.getByRole("button", { name: "创建邀请" }));
    await waitFor(() =>
      expect(platformApi.createInstitutionInvitation).toHaveBeenCalledWith(
        expect.objectContaining({
          institution_type: "HEALTH_STORE",
          administrative_region_id: 1001,
        }),
        expect.any(String),
      ),
    );
  });

  it("平台邀请支持重发新短码与撤销且显式携带版本", async () => {
    vi.mocked(platformApi.listInstitutionInvitations).mockResolvedValue([
      {
        invitation_id: "invitation-1",
        institution_name: "一期健康门店",
        institution_type: "HEALTH_STORE",
        status: "ISSUED",
        version: 1,
      },
    ]);
    vi.mocked(platformApi.resendInstitutionInvitation).mockResolvedValue({
      invitation_id: "invitation-1",
      institution_name: "一期健康门店",
      institution_type: "HEALTH_STORE",
      status: "ISSUED",
      version: 2,
      short_code: "000003",
    });
    vi.mocked(platformApi.revokeInstitutionInvitation).mockResolvedValue({
      invitation_id: "invitation-1",
      institution_name: "一期健康门店",
      institution_type: "HEALTH_STORE",
      status: "REVOKED",
      version: 2,
    });
    render(<InstitutionInvitationPage />);
    await userEvent.click(await screen.findByRole("button", { name: "重发" }));
    await userEvent.click(screen.getByRole("button", { name: "确认重发" }));
    await waitFor(() =>
      expect(platformApi.resendInstitutionInvitation).toHaveBeenCalledWith("invitation-1", 1, expect.any(String)),
    );
    await userEvent.click(screen.getByRole("button", { name: "撤销" }));
    await userEvent.click(screen.getByRole("button", { name: "确认撤销" }));
    await waitFor(() =>
      expect(platformApi.revokeInstitutionInvitation).toHaveBeenCalledWith("invitation-1", 1, expect.any(String)),
    );
  });

  it("平台审核可对具体证照发出受控补正指令", async () => {
    const review = {
      application_id: "application-1",
      status: "UNDER_REVIEW",
      version: 4,
      draft: {},
      materials: [
        {
          file_id: "file-1",
          license_type: "BUSINESS_LICENSE",
          status: "CLEAN",
        },
      ],
    };
    vi.mocked(platformApi.listInstitutionReviews).mockResolvedValue([review]);
    vi.mocked(platformApi.getInstitutionReviewDetail).mockResolvedValue(review);
    vi.mocked(platformApi.decideInstitutionReview).mockResolvedValue({
      ...review,
      status: "NEEDS_CORRECTION",
      version: 5,
    });
    render(<InstitutionReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    await userEvent.click(screen.getByLabelText("营业执照"));
    await userEvent.click(screen.getByRole("button", { name: "NEEDS_CORRECTION" }));
    await userEvent.click(screen.getByRole("button", { name: "确认补正" }));
    await waitFor(() =>
      expect(platformApi.decideInstitutionReview).toHaveBeenCalledWith(
        "application-1",
        expect.objectContaining({
          decision: "NEEDS_CORRECTION",
          correction_fields: ["business_license"],
          expected_version: 4,
        }),
        expect.any(String),
      ),
    );
  });

  it("平台审核可组合选择全部受控补正字段", async () => {
    const review = {
      application_id: "application-1",
      status: "UNDER_REVIEW",
      version: 4,
      draft: {},
      materials: [],
    };
    vi.mocked(platformApi.listInstitutionReviews).mockResolvedValue([review]);
    vi.mocked(platformApi.getInstitutionReviewDetail).mockResolvedValue(review);
    vi.mocked(platformApi.decideInstitutionReview).mockResolvedValue({
      ...review,
      status: "NEEDS_CORRECTION",
      version: 5,
    });
    render(<InstitutionReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    await userEvent.click(screen.getByLabelText("注册地址需补正"));
    await userEvent.click(screen.getByLabelText("联系电话需补正"));
    await userEvent.click(screen.getByRole("button", { name: "NEEDS_CORRECTION" }));
    await userEvent.click(screen.getByRole("button", { name: "确认补正" }));
    await waitFor(() =>
      expect(platformApi.decideInstitutionReview).toHaveBeenCalledWith(
        "application-1",
        expect.objectContaining({
          decision: "NEEDS_CORRECTION",
          correction_fields: ["registered_address", "contact_phone"],
        }),
        expect.any(String),
      ),
    );
  });

  it("平台真实审核队列与详情渲染不产生React列表key错误", async () => {
    const review = {
      application_id: "application-1",
      status: "UNDER_REVIEW",
      version: 4,
      draft: { registered_address: "合成注册地址" },
      materials: [
        { file_id: "shared-file", license_type: "BUSINESS_LICENSE", status: "CLEAN" },
        { file_id: "shared-file", license_type: "MEDICAL_INSTITUTION_LICENSE", status: "CLEAN" },
      ],
    };
    vi.mocked(platformApi.listInstitutionReviews).mockResolvedValue([review]);
    vi.mocked(platformApi.getInstitutionReviewDetail).mockResolvedValue(review);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    render(<InstitutionReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    await screen.findByText("合成注册地址");

    expect(consoleError).not.toHaveBeenCalled();
  });

  it("机构端使用真实文件与提交API完成草稿提交", async () => {
    vi.mocked(institutionApi.getOnboardingApplication).mockResolvedValue(draft);
    vi.mocked(institutionApi.saveOnboardingDraft).mockResolvedValue({
      ...draft,
      version: 2,
    });
    vi.mocked(institutionApi.initiatePrivateFileUpload).mockResolvedValue({
      file_id: "file-1",
    });
    vi.mocked(institutionApi.uploadPrivateFileContent).mockResolvedValue({
      uploaded: true,
    });
    vi.mocked(institutionApi.completePrivateFileUpload).mockResolvedValue({
      status: "PENDING_SCAN",
    });
    vi.mocked(institutionApi.getPrivateFileMetadata).mockResolvedValue({
      status: "CLEAN",
    });
    vi.mocked(institutionApi.submitOnboardingApplication).mockResolvedValue({
      ...draft,
      status: "SUBMITTED",
      version: 3,
    });
    render(<ControlledOnboardingPage />);
    expect((await screen.findAllByText("待填写")).length).toBeGreaterThan(0);
    for (const [label, value] of [
      ["统一社会信用代码", "91310000TEST000001"],
      ["法定代表人", "负责人"],
      ["注册地址", "测试注册地址"],
      ["服务地址", "测试服务地址"],
      ["联系人", "联系人"],
      ["联系电话", "test-contact"],
      ["联系邮箱", "slice1@example.invalid"],
      ["服务标签（逗号分隔）", "HYPERTENSION"],
    ] as const)
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    await userEvent.upload(
      screen.getByLabelText("营业执照"),
      new File(["safe"], "license.pdf", { type: "application/pdf" }),
    );
    fireEvent.change(screen.getByLabelText("营业执照有效期起"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("营业执照有效期止"), { target: { value: "2027-01-01" } });
    const form = screen.getByRole("button", { name: "上传材料并提交" }).closest("form");
    expect(form).not.toBeNull();
    if (form === null) throw new Error("controlled onboarding form missing");
    fireEvent.submit(form);
    await userEvent.click(screen.getByRole("button", { name: "确认上传并提交" }));
    await waitFor(() => expect(institutionApi.saveOnboardingDraft).toHaveBeenCalledOnce());
    await waitFor(() => expect(institutionApi.initiatePrivateFileUpload).toHaveBeenCalledOnce());
    await screen.findByText("申请已提交，等待平台审核。");
    expect(institutionApi.submitOnboardingApplication).toHaveBeenCalledWith(
      {
        expected_version: 2,
        licenses: [
          {
            license_type: "BUSINESS_LICENSE",
            private_file_id: "file-1",
            valid_from: "2026-01-01",
            valid_until: "2027-01-01",
          },
        ],
      },
      expect.any(String),
    );
  });

  it("机构端未选择证照时仍可独立保存草稿", async () => {
    vi.mocked(institutionApi.getOnboardingApplication).mockResolvedValue(draft);
    vi.mocked(institutionApi.saveOnboardingDraft).mockResolvedValue({ ...draft, version: 2 });
    render(<ControlledOnboardingPage />);
    for (const [label, value] of [
      ["统一社会信用代码", "TEST-CREDIT-CODE"],
      ["法定代表人", "负责人"],
      ["注册地址", "测试注册地址"],
      ["服务地址", "测试服务地址"],
      ["联系人", "联系人"],
      ["联系电话", ["139", "0000", "0000"].join("")],
      ["联系邮箱", "slice1@example.invalid"],
      ["服务标签（逗号分隔）", "HYPERTENSION"],
    ] as const)
      fireEvent.change(await screen.findByLabelText(label), { target: { value } });

    await userEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(institutionApi.saveOnboardingDraft).toHaveBeenCalledOnce());
    expect(institutionApi.initiatePrivateFileUpload).not.toHaveBeenCalled();
  });

  it("机构端按补正字段保留既有材料并调用真实重提API", async () => {
    const correction: OnboardingApplication = {
      ...draft,
      status: "NEEDS_CORRECTION",
      version: 5,
      correction_fields: ["registered_address"],
      correction_reason_code: "ADDRESS_REQUIRES_CORRECTION",
      draft: {
        credit_code: "TEST-CREDIT-CODE",
        legal_representative_name: "负责人",
        registered_address: "待补正地址",
        service_address: "服务地址",
        contact_name: "联系人",
        contact_phone: "test-contact",
        contact_email: "slice1@example.invalid",
        service_tags: ["HYPERTENSION"],
      },
      licenses: [{ license_type: "BUSINESS_LICENSE", private_file_id: "file-1", valid_from: "2026-01-01", valid_until: "2027-01-01" }],
    };
    vi.mocked(institutionApi.getOnboardingApplication).mockResolvedValue(correction);
    vi.mocked(institutionApi.resubmitOnboardingApplication).mockResolvedValue({
      ...correction,
      status: "SUBMITTED",
      version: 7,
    });
    render(<ControlledOnboardingPage />);
    expect((await screen.findAllByText("待补正")).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("注册地址"), {
      target: { value: "已补正地址" },
    });
    const form = screen.getByRole("button", { name: "补正并重新提交" }).closest("form");
    expect(form).not.toBeNull();
    if (form === null) throw new Error("controlled correction form missing");
    fireEvent.submit(form);
    await userEvent.click(screen.getByRole("button", { name: "确认重新提交" }));
    await waitFor(() => expect(institutionApi.resubmitOnboardingApplication).toHaveBeenCalledOnce());
    expect(institutionApi.resubmitOnboardingApplication).toHaveBeenCalledWith(
      expect.objectContaining({
        credit_code: "TEST-CREDIT-CODE",
        legal_representative_name: "负责人",
        expected_version: 5,
        registered_address: "已补正地址",
        service_address: "服务地址",
        contact_name: "联系人",
        contact_phone: "test-contact",
        contact_email: "slice1@example.invalid",
        service_tags: ["HYPERTENSION"],
        licenses: [{ license_type: "BUSINESS_LICENSE", private_file_id: "file-1", valid_from: "2026-01-01", valid_until: "2027-01-01" }],
      }),
      expect.any(String),
    );
    expect(institutionApi.initiatePrivateFileUpload).not.toHaveBeenCalled();
    expect(await screen.findByText("补正已重新提交，等待平台复核。")).toBeInTheDocument();
  });

  it("机构端按材料补正指令原子替换旧证照引用", async () => {
    const correction: OnboardingApplication = {
      ...draft,
      status: "NEEDS_CORRECTION",
      version: 5,
      correction_fields: ["business_license"],
      correction_reason_code: "BUSINESS_LICENSE_REQUIRES_CORRECTION",
      draft: {
        credit_code: "TEST-CREDIT-CODE",
        legal_representative_name: "负责人",
        registered_address: "测试注册地址",
        service_address: "测试服务地址",
        contact_name: "联系人",
        contact_phone: "test-contact",
        contact_email: "slice1@example.invalid",
        service_tags: ["HYPERTENSION"],
      },
      licenses: [{ license_type: "BUSINESS_LICENSE", private_file_id: "old-file", valid_from: "2025-01-01", valid_until: "2026-01-01" }],
    };
    vi.mocked(institutionApi.getOnboardingApplication).mockResolvedValue(correction);
    vi.mocked(institutionApi.initiatePrivateFileUpload).mockResolvedValue({
      file_id: "new-file",
    });
    vi.mocked(institutionApi.uploadPrivateFileContent).mockResolvedValue({
      uploaded: true,
    });
    vi.mocked(institutionApi.completePrivateFileUpload).mockResolvedValue({
      status: "PENDING_SCAN",
    });
    vi.mocked(institutionApi.getPrivateFileMetadata).mockResolvedValue({
      status: "CLEAN",
    });
    vi.mocked(institutionApi.resubmitOnboardingApplication).mockResolvedValue({
      ...correction,
      status: "SUBMITTED",
      version: 7,
      licenses: [{ license_type: "BUSINESS_LICENSE", private_file_id: "new-file", valid_from: "2026-01-01", valid_until: "2027-01-01" }],
    });
    render(<ControlledOnboardingPage />);
    await userEvent.upload(
      await screen.findByLabelText("营业执照"),
      new File(["replacement"], "replacement.pdf", {
        type: "application/pdf",
      }),
    );
    fireEvent.change(screen.getByLabelText("营业执照有效期起"), { target: { value: "2026-01-01" } });
    fireEvent.change(screen.getByLabelText("营业执照有效期止"), { target: { value: "2027-01-01" } });
    const replacementForm = screen.getByRole("button", { name: "补正并重新提交" }).closest("form");
    expect(replacementForm).not.toBeNull();
    if (replacementForm === null) throw new Error("replacement form missing");
    fireEvent.submit(replacementForm);
    await userEvent.click(screen.getByRole("button", { name: "确认重新提交" }));
    await waitFor(() => expect(institutionApi.initiatePrivateFileUpload).toHaveBeenCalledOnce(), { timeout: 5000 });
    await waitFor(
      () =>
        expect(institutionApi.resubmitOnboardingApplication).toHaveBeenCalledWith(
          expect.objectContaining({
            licenses: [
              {
                license_type: "BUSINESS_LICENSE",
                private_file_id: "new-file",
                valid_from: "2026-01-01",
                valid_until: "2027-01-01",
              },
            ],
          }),
          expect.any(String),
        ),
      { timeout: 5000 },
    );
  });

  it("平台审核以当前密码再认证并经JWT获取短时材料Blob", async () => {
    vi.mocked(platformApi.listInstitutionReviews).mockResolvedValue([
      { application_id: "application-1", status: "SUBMITTED", version: 3 },
    ]);
    vi.mocked(platformApi.getInstitutionReviewDetail).mockResolvedValue({
      application_id: "application-1",
      status: "SUBMITTED",
      version: 3,
      draft: { registered_address: "测试注册地址" },
      revisions: [],
      materials: [
        {
          file_id: "file-1",
          license_type: "BUSINESS_LICENSE",
          status: "CLEAN",
        },
      ],
    });
    vi.mocked(platformApi.requestPrivateFileAccess).mockResolvedValue({
      content_path: "/api/v1/private-files/file-1/content",
      access_credential: "opaque-private-file-credential-value",
      expires_at_epoch: 1_800_000_000,
    });
    vi.mocked(platformApi.fetchPrivateFileContent).mockResolvedValue(new Blob(["safe"], { type: "application/pdf" }));
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:safe-file");
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<InstitutionReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    expect(await screen.findByText("测试注册地址")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("当前账户密码"), "StrongPassword123!");
    await userEvent.click(screen.getByRole("button", { name: "受控查看材料" }));
    await waitFor(() =>
      expect(platformApi.requestPrivateFileAccess).toHaveBeenCalledWith("file-1", "StrongPassword123!"),
    );
    expect(platformApi.fetchPrivateFileContent).toHaveBeenCalledWith(
      "/api/v1/private-files/file-1/content",
      "opaque-private-file-credential-value",
    );
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(open).toHaveBeenCalledWith("blob:safe-file", "_blank", "noopener,noreferrer");
  });

  it("批准后同时展示Tenant已激活与服务尚未就绪", async () => {
    vi.mocked(institutionApi.getOnboardingApplication).mockResolvedValue({
      ...draft,
      status: "APPROVED",
      tenant_id: "tenant-101",
      tenant_active: true,
      service_ready: false,
    });
    render(<ControlledOnboardingPage />);
    expect(await screen.findByText("机构已通过入驻审核")).toBeInTheDocument();
    expect(screen.getByText("已激活")).toBeInTheDocument();
    expect(screen.getByText("尚未就绪")).toBeInTheDocument();
    expect(screen.getByText(/健康服务尚未开放/)).toBeInTheDocument();
  });

  it("已提交与审核中状态不再提供编辑或重复提交入口", async () => {
    for (const status of ["SUBMITTED", "UNDER_REVIEW"] as const) {
      vi.mocked(institutionApi.getOnboardingApplication).mockResolvedValue({
        ...draft,
        status,
      });
      const view = render(<ControlledOnboardingPage />);
      expect(await screen.findByText(status === "SUBMITTED" ? "申请已进入审核队列" : "申请审核中")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "上传材料并提交" })).not.toBeInTheDocument();
      view.unmount();
    }
  });

  it("固定错误状态提供安全且可恢复的用户文案", () => {
    const statuses = [401, 403, 404, 409, 422, 429, 503] as const;
    for (const status of statuses) {
      const message = onboardingErrorMessage(new ApiError(status, "UNKNOWN", null), "fallback");
      expect(message).not.toBe("fallback");
      expect(message.length).toBeGreaterThan(8);
    }
    expect(onboardingErrorMessage(new ApiError(409, "ONBOARDING_VERSION_CONFLICT", null), "fallback")).toContain(
      "页面已刷新",
    );
    expect(onboardingErrorMessage(new ApiError(503, "PRIVATE_FILE_SCANNER_UNAVAILABLE", null), "fallback")).toContain(
      "扫描服务暂时不可用",
    );
  });

  it("二次确认框支持初始焦点、循环Tab、Escape与焦点恢复", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)} type="button">
            准备提交
          </button>
          {open ? (
            <ConfirmDialog
              busy={false}
              confirmLabel="确认"
              description="确认后执行一次操作。"
              onClose={() => setOpen(false)}
              onConfirm={() => undefined}
              title="确认操作？"
            />
          ) : null}
        </>
      );
    }
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "准备提交" });
    await userEvent.click(trigger);
    const cancel = screen.getByRole("button", { name: "取消" });
    const confirm = screen.getByRole("button", { name: "确认" });
    const close = screen.getByRole("button", { name: "关闭确认框" });
    expect(cancel).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(close).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(confirm).toHaveFocus();
    await userEvent.tab();
    expect(close).toHaveFocus();
    await userEvent.tab();
    expect(cancel).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
