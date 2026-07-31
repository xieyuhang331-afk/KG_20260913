import { createBrowserRouter, Navigate } from "react-router-dom";
import { LoginPage } from "@/domains/auth/pages/LoginPage";
import { institutionRoutes } from "@/domains/institution/routes";
import { familyRoutes } from "@/domains/family/routes";
import { platformRoutes } from "@/domains/platform/routes";
import { therapistRoutes } from "@/domains/therapist/routes";
import { InstitutionShell } from "@/shells/InstitutionShell";
import { FamilyShell } from "@/shells/FamilyShell";
import { PlatformShell } from "@/shells/PlatformShell";
import { PublicShell } from "@/shells/PublicShell";
import { TherapistShell } from "@/shells/TherapistShell";
import { ProtectedRoute } from "./routeGuards";
import { PLATFORM_REVIEW_ROLES, USER_ROLES } from "@/shared/constants/roles";

function ForbiddenPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-tea px-4">
      <section className="rounded-lg border border-ink/10 bg-white p-6 text-center shadow-sm">
        <h1 className="text-2xl font-semibold text-ink">无权限访问</h1>
        <p className="mt-2 text-sm text-ink/60">当前账号不能进入这个端或页面。</p>
      </section>
    </main>
  );
}

function NotFoundPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-tea px-4">
      <section className="rounded-lg border border-ink/10 bg-white p-6 text-center shadow-sm">
        <h1 className="text-2xl font-semibold text-ink">页面不存在</h1>
        <p className="mt-2 text-sm text-ink/60">请检查访问路径。</p>
      </section>
    </main>
  );
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to="/login" replace />
  },
  {
    element: <PublicShell />,
    children: [
      { path: "/login", element: <LoginPage /> },
      { path: "/platform/login", element: <LoginPage /> },
      { path: "/institution/login", element: <LoginPage /> },
      { path: "/family/login", element: <LoginPage /> },
      { path: "/family/register", element: familyRoutes.register }
    ]
  },
  {
    element: <ProtectedRoute roles={[USER_ROLES.member]} />,
    children: [
      {
        path: "/family",
        element: <FamilyShell />,
        children: familyRoutes.protectedChildren
      }
    ]
  },
  {
    element: <ProtectedRoute roles={[USER_ROLES.orgAdmin]} />,
    children: [
      {
        path: "/institution",
        element: <InstitutionShell />,
        children: institutionRoutes.protectedChildren
      }
    ]
  },
  {
    element: <ProtectedRoute roles={PLATFORM_REVIEW_ROLES} />,
    children: [
      {
        path: "/platform",
        element: <PlatformShell />,
        children: platformRoutes.protectedChildren
      }
    ]
  },
  {
    element: <ProtectedRoute roles={[USER_ROLES.member]} />,
    children: [
      {
        path: "/therapist",
        element: <TherapistShell />,
        children: therapistRoutes.protectedChildren
      }
    ]
  },
  { path: "/403", element: <ForbiddenPage /> },
  { path: "*", element: <NotFoundPage /> }
]);
