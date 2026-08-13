import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TenantApplicationPage } from "./pages/TenantApplicationPage";

describe("机构端门店入驻申请表 V1", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("校验必填字段并把焦点移动到第一个错误", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "核对并提交" }));
    expect(await screen.findByText("请输入 2-30 个字符的门店名称")).toBeInTheDocument();
    expect(screen.getByLabelText("门店名称")).toHaveFocus();
  });

  it("医疗类门店要求许可证号，非医疗类不提交许可证号", async () => {
    renderPage();
    await fillRequiredFields({ type: "社区医院", licenseNo: "" });
    await userEvent.click(screen.getByRole("button", { name: "核对并提交" }));
    expect(await screen.findByText("医疗类门店必须填写医疗机构许可证号")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("门店类型"), "健康管理门店");
    expect(screen.queryByLabelText("医疗机构许可证号")).not.toBeInTheDocument();
  });

  it("只保存非敏感草稿并在刷新后恢复", async () => {
    const view = renderPage();
    await userEvent.type(screen.getByLabelText("门店名称"), "康邻测试门店");
    await userEvent.selectOptions(screen.getByLabelText("门店类型"), "健康管理门店");
    await userEvent.type(screen.getByLabelText("统一社会信用代码"), "91330100MA12345678");
    await userEvent.type(screen.getByLabelText("联系人手机"), "13800138000");
    await userEvent.type(screen.getByLabelText("省"), "浙江省");
    view.unmount();

    const stored = sessionStorage.getItem("institution-tenant-application-draft-v1") ?? "";
    expect(stored).toContain("康邻测试门店");
    expect(stored).toContain("浙江省");
    expect(stored).not.toContain("91330100MA12345678");
    expect(stored).not.toContain("13800138000");

    renderPage();
    expect(screen.getByLabelText("门店名称")).toHaveValue("康邻测试门店");
    expect(screen.getByLabelText("省")).toHaveValue("浙江省");
    expect(screen.getByLabelText("统一社会信用代码")).toHaveValue("");
  });

  it("校验附件但不伪造上传成功或提交本机文件", async () => {
    renderPage();
    expect(screen.getByText("LICENSE UPLOAD: WAITING FOR BACKEND CONTRACT")).toBeInTheDocument();
    const input = screen.getByLabelText("选择证照文件");
    fireEvent.change(input, {
      target: { files: [new File(["x"], "license.exe", { type: "application/octet-stream" })] },
    });
    expect(await screen.findByText("仅支持 JPG、PNG 或 PDF 文件")).toBeInTheDocument();
    expect(screen.queryByText("上传成功")).not.toBeInTheDocument();
  });

  it("二次确认后提交精确合同，阻止重复点击并跳转状态页", async () => {
    let resolveRequest!: (response: Response) => void;
    const fetchMock = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveRequest = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await fillRequiredFields({ type: "健康管理门店" });

    await userEvent.click(screen.getByRole("button", { name: "核对并提交" }));
    expect(screen.getByRole("dialog", { name: "确认提交门店申请" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "确认提交" }));
    expect(screen.getByRole("button", { name: "正在提交" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const [, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(options.body))).toEqual({
      name: "康邻测试门店",
      type: "健康管理门店",
      credit_code: "91330100MA12345678",
      license_no: null,
      license_image: null,
      legal_person_name: "合成法人",
      province: "浙江省",
      city: "杭州市",
      district: "滨江区",
      address: "合成路一号测试园区",
      contact_name: "合成联系人",
      contact_phone: "13800138000",
      contact_email: "test@example.invalid",
      attachments: [],
    });
    expect(String(options.body)).not.toContain("service_area");
    expect(String(options.body)).not.toContain("file_url");

    resolveRequest(
      jsonResponse(200, { id: 42, tenant_code: "T42", name: "康邻测试门店", status: "pending", attachment_count: 0 }),
    );
    expect(await screen.findByText("STATUS PAGE 42")).toBeInTheDocument();
    expect(sessionStorage.getItem("institution-tenant-application-draft-v1")).toBeNull();
  });

  it("按 Enter 只能打开确认弹窗，不能绕过确认直接提交", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderPage();
    await fillRequiredFields({ type: "健康管理门店" });
    const form = screen.getByLabelText("联系邮箱").closest("form");
    if (!form) throw new Error("application form missing");
    fireEvent.submit(form);
    expect(await screen.findByRole("dialog", { name: "确认提交门店申请" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续检查" })).toHaveFocus();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("确认弹窗限制键盘焦点，Escape 关闭并恢复触发按钮焦点", async () => {
    renderPage();
    await fillRequiredFields({ type: "健康管理门店" });
    const trigger = screen.getByRole("button", { name: "核对并提交" });
    await userEvent.click(trigger);
    const cancel = await screen.findByRole("button", { name: "继续检查" });
    const confirm = screen.getByRole("button", { name: "确认提交" });
    expect(cancel).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(confirm).toHaveFocus();
    await userEvent.tab();
    expect(cancel).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "确认提交门店申请" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it.each([
    [409, "该统一社会信用代码已有申请，请核对后查看申请记录"],
    [422, "部分字段未通过服务端校验，请检查标记项后重试"],
    [503, "申请服务暂时不可用，已保留填写内容，请稍后重试"],
  ])("安全映射 HTTP %s", async (status, message) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(status, { detail: "server detail must not leak" })),
    );
    renderPage();
    await fillRequiredFields({ type: "健康管理门店" });
    await userEvent.click(screen.getByRole("button", { name: "核对并提交" }));
    await userEvent.click(screen.getByRole("button", { name: "确认提交" }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByLabelText("门店名称")).toHaveValue("康邻测试门店");
  });
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/institution/store/application"]}>
      <Routes>
        <Route path="/institution/store/application" element={<TenantApplicationPage />} />
        <Route path="/institution/store/application/:tenantId/status" element={<div>STATUS PAGE 42</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function fillRequiredFields({ type, licenseNo }: { type: string; licenseNo?: string }) {
  await userEvent.type(screen.getByLabelText("门店名称"), "康邻测试门店");
  await userEvent.selectOptions(screen.getByLabelText("门店类型"), type);
  await userEvent.type(screen.getByLabelText("统一社会信用代码"), "91330100MA12345678");
  if (licenseNo !== undefined && screen.queryByLabelText("医疗机构许可证号")) {
    if (licenseNo) await userEvent.type(screen.getByLabelText("医疗机构许可证号"), licenseNo);
  }
  await userEvent.type(screen.getByLabelText("法定代表人"), "合成法人");
  await userEvent.type(screen.getByLabelText("省"), "浙江省");
  await userEvent.type(screen.getByLabelText("市"), "杭州市");
  await userEvent.type(screen.getByLabelText("区/县"), "滨江区");
  await userEvent.type(screen.getByLabelText("详细地址"), "合成路一号测试园区");
  await userEvent.type(screen.getByLabelText("联系人姓名"), "合成联系人");
  await userEvent.type(screen.getByLabelText("联系人手机"), "13800138000");
  await userEvent.type(screen.getByLabelText("联系邮箱"), "test@example.invalid");
}

function jsonResponse(status: number, data: unknown) {
  return new Response(JSON.stringify(status < 400 ? { code: 0, message: "ok", data } : data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
