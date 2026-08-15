import { Navigate, type RouteObject } from "react-router-dom";
import { ApplicationDetailPage } from "./pages/ApplicationDetailPage";
import { MyApplicationsPage } from "./pages/MyApplicationsPage";
import { InstitutionOrganizationPage } from "@/domains/organization/pages/机构组织资料页";
import { ControlledOnboardingPage } from "./pages/ControlledOnboardingPage";

export const institutionRoutes: { protectedChildren: RouteObject[] } = {
  protectedChildren: [
    { index: true, element: <Navigate to="/institution/store/applications" replace /> },
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
  ],
};
