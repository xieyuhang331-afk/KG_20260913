import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { availableCaseActions } from "@/domains/institution/pages/ServiceFulfillmentPage";
import { availableTransferActions } from "@/domains/institution/pages/ServiceTransferPage";
import { DataExportOversightPage, exportStatusLabel } from "./pages/DataExportOversightPage";
import { allPlatformNavigation } from "./navigation";
import { platformRoutes } from "./routes";

describe("一期切片7平台履约转机构与导出监督", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });
  it("注册平台监督列表和详情路由", () => {
    expect(platformRoutes.protectedChildren.flatMap((route) => route.children ?? [])).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ path: "service-fulfillment" }),
        expect.objectContaining({ path: "service-fulfillment/:caseId" }),
        expect.objectContaining({ path: "service-transfers" }),
        expect.objectContaining({ path: "service-transfers/:transferId" }),
        expect.objectContaining({ path: "data-exports" }),
        expect.objectContaining({ path: "data-exports/:exportId" }),
      ]),
    );
  });

  it("平台导航仅展示真实监督能力", () => {
    expect(allPlatformNavigation.map((item) => item.label)).toEqual(
      expect.arrayContaining(["履约监督", "转机构监督", "导出监督"]),
    );
    expect(allPlatformNavigation.map((item) => item.label)).not.toContain("下载客户数据");
  });

  it("安全终止和协调关闭严格受状态约束", () => {
    expect(availableCaseActions("ACTIVE", "platform")).toEqual(["safety-terminate"]);
    expect(availableCaseActions("COMPLETED", "platform")).toEqual([]);
    expect(availableTransferActions("USER_SCOPE_CONFIRMED", "platform")).toEqual(["coordinate-close"]);
    expect(availableTransferActions("ACCEPTED", "platform")).toEqual([]);
  });

  it("导出状态使用安全中文且未知值fail-closed", () => {
    expect(exportStatusLabel("READY")).toBe("已准备");
    expect(exportStatusLabel("UNKNOWN")).toBe("状态待核对");
  });

  it("导出监督只展示元数据且不展示完整公共标识或下载入口", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(success(exportPage())));
    render(
      <MemoryRouter initialEntries={["/platform/data-exports"]}>
        <Routes>
          <Route element={<DataExportOversightPage />} path="/platform/data-exports" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("个人数据导出 00000731")).toBeInTheDocument();
    expect(screen.getAllByText("已准备")).toHaveLength(2);
    expect(screen.getByText("健康档案摘要、健康指标")).toBeInTheDocument();
    expect(screen.queryByText(exportId)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /下载/ })).not.toBeInTheDocument();
  });

  it("503保持依赖不可用语义而不包装为成功", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(failure(503, "DEPENDENCY_UNAVAILABLE")));
    render(
      <MemoryRouter initialEntries={["/platform/data-exports"]}>
        <Routes>
          <Route element={<DataExportOversightPage />} path="/platform/data-exports" />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂时不可用");
    expect(screen.queryByText(/个人数据导出 [0-9A-F]{8}/)).not.toBeInTheDocument();
  });
});

const exportId = "0198d6a1-1111-7abc-8000-000000000731";

function exportPage() {
  return {
    items: [
      {
        export_id: exportId,
        subject_member_id: "0198d6a1-1111-7abc-8000-000000000732",
        status: "READY",
        requested_scope: ["PROFILE", "CANONICAL_FACT"],
        requested_at: "2026-08-29T08:00:00Z",
        ready_at: "2026-08-29T08:02:00Z",
        expires_at: "2026-08-29T10:00:00Z",
        downloaded_at: null,
        version: 2,
      },
    ],
    next_cursor: null,
  };
}

function success(data: unknown) {
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
