import { Navigate, type RouteObject } from "react-router-dom";
import { ApplicationDetailPage } from "./pages/ApplicationDetailPage";
import { MyApplicationsPage } from "./pages/MyApplicationsPage";
import { TenantApplicationPage } from "./pages/TenantApplicationPage";
import { InstitutionOrganizationPage } from "@/domains/organization/pages/机构组织资料页";

export const institutionRoutes: { protectedChildren: RouteObject[] } = {
  protectedChildren: [
    { index: true, element: <Navigate to="/institution/store/applications" replace /> },
    {
      path: "store/applications",
      element: <MyApplicationsPage />,
    },
    {
      path: "store/application",
      element: <TenantApplicationPage />,
    },
    {
      path: "store/application/:tenantId/status",
      element: <ApplicationDetailPage />,
    },
    { path: "organization", element: <InstitutionOrganizationPage /> },
  ],
};
