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
    [USER_ROLES.orgAdmin, "/institution/store/applications", "INSTITUTION TARGET"],
    [USER_ROLES.provinceAdmin, "/platform/home", "PLATFORM TARGET"],
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
