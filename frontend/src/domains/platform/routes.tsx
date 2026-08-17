import { Navigate, type RouteObject } from "react-router-dom";
import { ProtectedRoute } from "@/app/routeGuards";
import { USER_ROLES } from "@/shared/constants/roles";
import { PlatformHomePage } from "./pages/PlatformHomePage";
import { TenantReviewDetailPage } from "./pages/TenantReviewDetailPage";
import { TenantReviewListPage } from "./pages/TenantReviewListPage";
import { IdentityReviewListPage } from "./pages/实名认证审核列表页";
import { IdentityReviewDetailPage } from "./pages/实名认证审核详情页";
import { PlatformOrganizationPage } from "@/domains/organization/pages/平台组织治理页";
import { InstitutionInvitationPage } from "./pages/InstitutionInvitationPage";
import { InstitutionReviewPage } from "./pages/InstitutionReviewPage";
import { TherapistReviewPage } from "./pages/TherapistReviewPage";
import { TherapistStatusPage } from "./pages/TherapistStatusPage";

export const platformRoutes: { protectedChildren: RouteObject[] } = {
  protectedChildren: [
    { index: true, element: <Navigate to="/platform/home" replace /> },
    { path: "home", element: <PlatformHomePage /> },
    { path: "stores/reviews", element: <TenantReviewListPage /> },
    { path: "stores/reviews/:tenantId", element: <TenantReviewDetailPage /> },
    {
      element: <ProtectedRoute roles={[USER_ROLES.superAdmin, USER_ROLES.provinceAdmin, USER_ROLES.cityAdmin]} />,
      children: [
        { path: "organizations", element: <PlatformOrganizationPage /> },
        { path: "organizations/:organizationId", element: <PlatformOrganizationPage /> },
      ],
    },
    {
      element: <ProtectedRoute roles={[USER_ROLES.superAdmin]} />,
      children: [
        { path: "identity-reviews", element: <IdentityReviewListPage /> },
        { path: "identity-reviews/:userId", element: <IdentityReviewDetailPage /> },
        { path: "institution-invitations", element: <InstitutionInvitationPage /> },
        { path: "institution-reviews", element: <InstitutionReviewPage /> },
        { path: "therapist-reviews", element: <TherapistReviewPage /> },
        { path: "therapist-status", element: <TherapistStatusPage /> },
      ],
    },
  ],
};
