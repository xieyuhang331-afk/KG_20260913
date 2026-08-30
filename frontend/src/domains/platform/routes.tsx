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
import { ConsentDocumentPage } from "./pages/ConsentDocumentPage";
import { HealthPlanTemplatePage } from "./pages/HealthPlanTemplatePage";
import { HealthPlanReviewPage } from "./pages/HealthPlanReviewPage";
import { useAuthStore } from "@/shared/auth/authStore";
import { ServiceFulfillmentOversightPage } from "./pages/ServiceFulfillmentOversightPage";
import { ServiceTransferOversightPage } from "./pages/ServiceTransferOversightPage";
import { DataExportOversightPage } from "./pages/DataExportOversightPage";
import { HighRiskOversightPage } from "./pages/HighRiskOversightPage";

function PlatformIndexRedirect() {
  const { currentUser } = useAuthStore();
  return (
    <Navigate
      to={
        currentUser?.role === USER_ROLES.expert
          ? "/platform/health-plan-reviews"
          : currentUser?.role === USER_ROLES.sysAdmin
            ? "/platform/health-plan-templates"
            : "/platform/home"
      }
      replace
    />
  );
}

export const platformRoutes: { protectedChildren: RouteObject[] } = {
  protectedChildren: [
    { index: true, element: <PlatformIndexRedirect /> },
    {
      element: <ProtectedRoute roles={[USER_ROLES.superAdmin, USER_ROLES.provinceAdmin, USER_ROLES.cityAdmin]} />,
      children: [
        { path: "home", element: <PlatformHomePage /> },
        { path: "stores/reviews", element: <TenantReviewListPage /> },
        { path: "stores/reviews/:tenantId", element: <TenantReviewDetailPage /> },
      ],
    },
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
        { path: "identity-reviews/:reviewId", element: <IdentityReviewDetailPage /> },
        { path: "consent-documents", element: <ConsentDocumentPage /> },
        { path: "institution-invitations", element: <InstitutionInvitationPage /> },
        { path: "institution-reviews", element: <InstitutionReviewPage /> },
        { path: "therapist-reviews", element: <TherapistReviewPage /> },
        { path: "therapist-status", element: <TherapistStatusPage /> },
      ],
    },
    {
      element: <ProtectedRoute roles={[USER_ROLES.expert, USER_ROLES.sysAdmin, USER_ROLES.superAdmin]} />,
      children: [
        { path: "health-plan-templates", element: <HealthPlanTemplatePage /> },
        { path: "health-plan-templates/:templateVersionId", element: <HealthPlanTemplatePage /> },
      ],
    },
    {
      element: <ProtectedRoute roles={[USER_ROLES.expert]} />,
      children: [
        { path: "health-plan-reviews", element: <HealthPlanReviewPage /> },
        { path: "health-plan-reviews/:reviewId", element: <HealthPlanReviewPage /> },
      ],
    },
    {
      element: <ProtectedRoute roles={[USER_ROLES.superAdmin, USER_ROLES.sysAdmin]} />,
      children: [
        { path: "service-fulfillment", element: <ServiceFulfillmentOversightPage /> },
        { path: "service-fulfillment/:caseId", element: <ServiceFulfillmentOversightPage /> },
        { path: "service-transfers", element: <ServiceTransferOversightPage /> },
        { path: "service-transfers/:transferId", element: <ServiceTransferOversightPage /> },
        { path: "data-exports", element: <DataExportOversightPage /> },
        { path: "data-exports/:exportId", element: <DataExportOversightPage /> },
        { path: "high-risk-tasks", element: <HighRiskOversightPage /> },
        { path: "high-risk-tasks/:taskId", element: <HighRiskOversightPage /> },
      ],
    },
  ],
};
