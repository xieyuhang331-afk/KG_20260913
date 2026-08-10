import { Navigate, type RouteObject } from "react-router-dom";
import { PlaceholderPage } from "@/shared/ui/PlaceholderPage";
import { ApplicationDetailPage } from "./pages/ApplicationDetailPage";
import { MyApplicationsPage } from "./pages/MyApplicationsPage";
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
      element: <PlaceholderPage title="门店入驻申请" description="下一阶段接入 POST /api/v1/tenants。" />,
    },
    {
      path: "store/application/:tenantId/status",
      element: <ApplicationDetailPage />,
    },
    { path: "organization", element: <InstitutionOrganizationPage /> },
  ],
};
