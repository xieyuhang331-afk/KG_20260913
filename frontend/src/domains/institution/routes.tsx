import { Navigate, type RouteObject } from "react-router-dom";
import { ApplicationDetailPage } from "./pages/ApplicationDetailPage";
import { MyApplicationsPage } from "./pages/MyApplicationsPage";
import { InstitutionOrganizationPage } from "@/domains/organization/pages/机构组织资料页";
import { ControlledOnboardingPage } from "./pages/ControlledOnboardingPage";
import { TherapistInvitationPage } from "./pages/TherapistInvitationPage";
import { TherapistListPage } from "./pages/TherapistListPage";
import { ServiceReadinessPage } from "./pages/ServiceReadinessPage";
import { MemberInvitationPage } from "./pages/MemberInvitationPage";
import { MemberEnrollmentPage } from "./pages/MemberEnrollmentPage";
import { MemberEnrollmentDetailPage } from "./pages/MemberEnrollmentDetailPage";
import { HealthRecordPage } from "./pages/HealthRecordPage";
import { HealthPlanPage } from "./pages/HealthPlanPage";
import { ServiceFulfillmentPage } from "./pages/ServiceFulfillmentPage";
import { ServiceTransferPage } from "./pages/ServiceTransferPage";

export const institutionRoutes: { protectedChildren: RouteObject[] } = {
  protectedChildren: [
    { index: true, element: <Navigate to="/institution/store/application" replace /> },
    {
      path: "store/applications",
      element: <MyApplicationsPage />,
    },
    {
      path: "store/application",
      element: <ControlledOnboardingPage />,
    },
    {
      path: "store/application/:tenantId/status",
      element: <ApplicationDetailPage />,
    },
    { path: "organization", element: <InstitutionOrganizationPage /> },
    { path: "therapist-invitations", element: <TherapistInvitationPage /> },
    { path: "therapists", element: <TherapistListPage /> },
    { path: "service-readiness", element: <ServiceReadinessPage /> },
    { path: "member-invitations", element: <MemberInvitationPage /> },
    { path: "member-enrollments", element: <MemberEnrollmentPage /> },
    { path: "member-enrollments/:enrollmentId", element: <MemberEnrollmentDetailPage /> },
    { path: "service-cases/:caseId/health-record", element: <HealthRecordPage /> },
    { path: "plan-generations/:requestId", element: <HealthPlanPage mode="generation" /> },
    { path: "service-cases/:caseId/plans", element: <HealthPlanPage mode="list" /> },
    { path: "plans/:planId", element: <HealthPlanPage mode="detail" /> },
    { path: "service-cases", element: <ServiceFulfillmentPage /> },
    { path: "service-cases/:caseId/fulfillment", element: <ServiceFulfillmentPage /> },
    { path: "service-transfers", element: <ServiceTransferPage /> },
    { path: "service-transfers/:transferId", element: <ServiceTransferPage /> },
  ],
};
