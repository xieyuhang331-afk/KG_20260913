import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConsentDocumentPage } from "./pages/ConsentDocumentPage";
import { IdentityReviewListPage } from "./pages/实名认证审核列表页";
import { platformNavigation } from "./navigation";

describe("平台用户实名与同意文档", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("实名队列使用用户审核业务语言而非会员身份", () => {
    render(<IdentityReviewListPage />, { wrapper: MemoryRouter });
    expect(screen.getByRole("heading", { name: "用户实名认证审核" })).toBeInTheDocument();
    expect(screen.queryByText(/会员实名认证/)).not.toBeInTheDocument();
  });

  it("平台正式导航使用用户实名术语并保留原路由", () => {
    expect(platformNavigation).toEqual(
      expect.arrayContaining([expect.objectContaining({ label: "用户实名审核", path: "/platform/identity-reviews" })]),
    );
    expect(platformNavigation.map((item) => item.label)).not.toContain("会员实名审核");
  });

  it("同意文档使用业务语言说明中文正文、重新同意与生效规则", () => {
    render(<ConsentDocumentPage />, { wrapper: MemoryRouter });
    expect(screen.getByRole("heading", { name: "同意文档" })).toBeInTheDocument();
    expect(screen.getByLabelText("简体中文标题")).toBeInTheDocument();
    expect(screen.getByLabelText("简体中文正文")).toBeInTheDocument();
    expect(screen.getAllByText(/重新同意/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/rendition|requires_reconsent|列表 API/)).not.toBeInTheDocument();
  });

  it("发布发生 409 时清除陈旧副本且不自动重放", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    const documentId = "0198b963-38f0-7d7d-8000-000000000041";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          document_version_id: documentId,
          document_type: "USER_AGREEMENT",
          semantic_version: "1.0.0",
          status: "DRAFT",
          requires_reconsent: true,
          effective_at: null,
          retired_at: null,
          renditions: [
            {
              rendition_id: "0198b963-38f0-7d7d-8000-000000000042",
              locale: "zh-CN",
              title: "合成协议",
              content_sha256: "a".repeat(64),
            },
          ],
          version: 1,
        }),
      )
      .mockResolvedValueOnce(failure(409, "VERSION_CONFLICT"));
    vi.stubGlobal("fetch", fetchMock);
    render(<ConsentDocumentPage />, { wrapper: MemoryRouter });

    await userEvent.type(screen.getByLabelText("语义版本"), "1.0.0");
    await userEvent.type(screen.getByLabelText("简体中文标题"), "合成协议");
    await userEvent.type(screen.getByLabelText("简体中文正文"), "仅用于前端合同验证。");
    await userEvent.click(screen.getByRole("button", { name: "创建草稿" }));
    await screen.findByText("同意文档草稿已创建。");
    await userEvent.type(screen.getByLabelText(/生效时间/), "2026-08-21T00:00");
    await userEvent.click(screen.getByRole("button", { name: "发布版本" }));

    expect(await screen.findByText(/页面已清除陈旧副本/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/publish"))).toHaveLength(1);
    expect(screen.getAllByText(/当前合同暂不支持查询历史文档/).length).toBeGreaterThanOrEqual(1);
  });
});

function response(data: unknown) {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function failure(status: number, code: string) {
  return new Response(JSON.stringify({ code }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
