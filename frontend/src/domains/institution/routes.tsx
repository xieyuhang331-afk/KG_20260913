import { Navigate, type RouteObject } from "react-router-dom";
import { PlaceholderPage } from "@/shared/ui/PlaceholderPage";
import { ApplicationDetailPage } from "./pages/ApplicationDetailPage";
import { MyApplicationsPage } from "./pages/MyApplicationsPage";

export const institutionRoutes: { protectedChildren: RouteObject[] } = {
  protectedChildren: [
    { index: true, element: <Navigate to="/institution/store/applications" replace /> },
    {
      path: "store/applications",
      element: <MyApplicationsPage />
    },
    {
      path: "store/application",
      element: <PlaceholderPage title="门店入驻申请" description="下一阶段接入 POST /api/v1/tenants。" />
    },
    {
      path: "store/application/:tenantId/status",
      element: <ApplicationDetailPage />
    }
  ]
};
