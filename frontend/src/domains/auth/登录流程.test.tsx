import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LoginPage } from "./pages/LoginPage";
import { setCurrentUser } from "@/shared/auth/authStore";
import { USER_ROLES, type UserRole } from "@/shared/constants/roles";

describe("Credential-safe login routing", () => {
  afterEach(() => {
    window.localStorage.clear();
    setCurrentUser(null);
    vi.unstubAllGlobals();
  });

  it.each([
    [USER_ROLES.orgAdmin, "/institution/store/application", "INSTITUTION TARGET"],
    [USER_ROLES.provinceAdmin, "/platform/home", "PLATFORM TARGET"],
    [USER_ROLES.healthExpert, "/platform/health-plan-reviews", "EXPERT TARGET"],
  ])("stores a session and routes %s to the authorized workspace", async (role, target, marker) => {
    mockLogin(role);
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path={target} element={<div>{marker}</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByLabelText("手机号或账号"), crypto.randomUUID());
    await userEvent.type(screen.getByLabelText("密码"), crypto.randomUUID());
    await userEvent.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText(marker)).toBeInTheDocument();
    expect(window.localStorage.getItem("kanglin.access_token") !== null).toBe(true);
  });

  it.each([
    ["", undefined],
    ["000002", "000002"],
  ])("normalizes optional TOTP at the login request boundary", async (totpCode, expectedTotpCode) => {
    mockLogin(USER_ROLES.provinceAdmin);
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/platform/home" element={<div>PLATFORM TARGET</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await userEvent.type(screen.getByLabelText("手机号或账号"), crypto.randomUUID());
    await userEvent.type(screen.getByLabelText("密码"), crypto.randomUUID());
    if (totpCode) await userEvent.type(screen.getByLabelText("TOTP（平台及机构人员）"), totpCode);
    await userEvent.click(screen.getByRole("button", { name: "登录" }));
    await screen.findByText("PLATFORM TARGET");

    const requestBody = JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body)) as Record<string, unknown>;
    if (expectedTotpCode === undefined) {
      expect(requestBody).not.toHaveProperty("totp_code");
    } else {
      expect(requestBody).toHaveProperty("totp_code", expectedTotpCode);
    }
  });
});

function mockLogin(role: UserRole) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 0,
          message: "ok",
          data: {
            access_token: crypto.randomUUID(),
            token_type: "bearer",
            expires_in: 1800,
            user: {
              id: 1,
              role,
              tenant_id: role === USER_ROLES.orgAdmin ? 501 : null,
              org_id: role === USER_ROLES.orgAdmin ? 41 : null,
            },
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
}
