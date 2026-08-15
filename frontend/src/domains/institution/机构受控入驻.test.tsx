import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as platformApi from "../platform/api";
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

vi.mock("./api");
vi.mock("../platform/api");

const draft: OnboardingApplication = {
  application_id: "application-1",
  institution_type: "HEALTH_STORE",
  status: "DRAFT",
  draft: {},
  correction_fields: [],
  version: 1,
  licenses: [],
};

beforeEach(() => {
  vi.resetAllMocks();
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
      /\b(?:localStorage|sessionStorage)\b/,
      /\bconsole\.(?:debug|info|log|warn|error)\s*\(/,
    ];
    for (const source of sources) {
      for (const pattern of forbidden) expect(source).not.toMatch(pattern);
    }
  });

  it("机构端与平台端路由使用受控合同", () => {
    expect(institutionRoutes.protectedChildren.some((route) => route.path === "store/application")).toBe(true);
    const protectedRoutes = platformRoutes.protectedChildren.flatMap((route) => route.children ?? []);
    expect(protectedRoutes.some((route) => route.path === "institution-invitations")).toBe(true);
    expect(protectedRoutes.some((route) => route.path === "institution-reviews")).toBe(true);
  });

  it("机构激活将邀请短码与TOTP作为显式受控请求", async () => {
    vi.mocked(institutionApi.activateInstitution).mockResolvedValue({
      application_id: "application-1",
      status: "DRAFT",
    });
    render(<InstitutionActivationPage />);
    for (const [label, value] of [
      ["邀请 ID", "0198a58f-4900-7000-8000-000000000001"],
      ["冻结手机号", "synthetic-mobile"],
      ["6 位短码", "000001"],
      ["密码", "StrongPassword123!"],
      ["TOTP 密钥", "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"],
      ["当前 TOTP", "000002"],
    ] as const)
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    await userEvent.click(screen.getByRole("button", { name: "激活" }));
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
      target: { value: "synthetic-mobile" },
    });
    fireEvent.change(screen.getByPlaceholderText("试点批次"), {
      target: { value: "PILOT-01" },
    });
    fireEvent.change(screen.getByPlaceholderText("区县节点 ID"), {
      target: { value: "41" },
    });
    await userEvent.click(screen.getByRole("button", { name: "创建邀请" }));
    await waitFor(() =>
      expect(platformApi.createInstitutionInvitation).toHaveBeenCalledWith(
        expect.objectContaining({
          institution_type: "HEALTH_STORE",
          administrative_region_id: 41,
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
    await userEvent.click(await screen.findByRole("button", { name: "重发邀请" }));
    await waitFor(() =>
      expect(platformApi.resendInstitutionInvitation).toHaveBeenCalledWith("invitation-1", 1, expect.any(String)),
    );
    await userEvent.click(screen.getByRole("button", { name: "撤销邀请" }));
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
    await userEvent.click(await screen.findByRole("button", { name: "要求补正此材料" }));
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
    await screen.findByText("状态：DRAFT");
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
    const form = screen.getByRole("button", { name: "上传材料并提交" }).closest("form");
    expect(form).not.toBeNull();
    if (form === null) throw new Error("controlled onboarding form missing");
    fireEvent.submit(form);
    await waitFor(() => expect(institutionApi.saveOnboardingDraft).toHaveBeenCalledOnce());
    await waitFor(() => expect(institutionApi.initiatePrivateFileUpload).toHaveBeenCalledOnce());
    await screen.findByText("申请已提交");
    expect(institutionApi.submitOnboardingApplication).toHaveBeenCalledOnce();
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
      licenses: [{ license_type: "BUSINESS_LICENSE", private_file_id: "file-1" }],
    };
    vi.mocked(institutionApi.getOnboardingApplication).mockResolvedValue(correction);
    vi.mocked(institutionApi.resubmitOnboardingApplication).mockResolvedValue({
      ...correction,
      status: "SUBMITTED",
      version: 7,
    });
    render(<ControlledOnboardingPage />);
    await screen.findByText("状态：NEEDS_CORRECTION");
    fireEvent.change(screen.getByLabelText("注册地址"), {
      target: { value: "已补正地址" },
    });
    const form = screen.getByRole("button", { name: "补正并重新提交" }).closest("form");
    expect(form).not.toBeNull();
    if (form === null) throw new Error("controlled correction form missing");
    fireEvent.submit(form);
    await waitFor(() => expect(institutionApi.resubmitOnboardingApplication).toHaveBeenCalledOnce());
    expect(institutionApi.resubmitOnboardingApplication).toHaveBeenCalledWith(
      expect.objectContaining({
        expected_version: 5,
        registered_address: "已补正地址",
        licenses: [{ license_type: "BUSINESS_LICENSE", private_file_id: "file-1" }],
      }),
      expect.any(String),
    );
    expect(institutionApi.initiatePrivateFileUpload).not.toHaveBeenCalled();
    expect(await screen.findByText("申请已提交")).toBeInTheDocument();
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
      licenses: [{ license_type: "BUSINESS_LICENSE", private_file_id: "old-file" }],
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
      licenses: [{ license_type: "BUSINESS_LICENSE", private_file_id: "new-file" }],
    });
    render(<ControlledOnboardingPage />);
    await userEvent.upload(
      await screen.findByLabelText("营业执照"),
      new File(["replacement"], "replacement.pdf", {
        type: "application/pdf",
      }),
    );
    const replacementForm = screen.getByRole("button", { name: "补正并重新提交" }).closest("form");
    expect(replacementForm).not.toBeNull();
    if (replacementForm === null) throw new Error("replacement form missing");
    fireEvent.submit(replacementForm);
    await waitFor(() => expect(institutionApi.initiatePrivateFileUpload).toHaveBeenCalledOnce(), { timeout: 5000 });
    await waitFor(
      () =>
        expect(institutionApi.resubmitOnboardingApplication).toHaveBeenCalledWith(
          expect.objectContaining({
            licenses: [
              {
                license_type: "BUSINESS_LICENSE",
                private_file_id: "new-file",
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
      access_path: "/api/v1/private-files/file-1/content?token=opaque",
    });
    vi.mocked(platformApi.fetchPrivateFileContent).mockResolvedValue(new Blob(["safe"], { type: "application/pdf" }));
    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:safe-file");
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    render(<InstitutionReviewPage />);
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    expect(await screen.findByText("注册地址：测试注册地址")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("当前账户密码"), "StrongPassword123!");
    await userEvent.click(screen.getByRole("button", { name: "受控查看材料" }));
    await waitFor(() =>
      expect(platformApi.requestPrivateFileAccess).toHaveBeenCalledWith("file-1", "StrongPassword123!"),
    );
    expect(platformApi.fetchPrivateFileContent).toHaveBeenCalledWith(
      "/api/v1/private-files/file-1/content?token=opaque",
    );
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(open).toHaveBeenCalledWith("blob:safe-file", "_blank", "noopener,noreferrer");
  });
});
