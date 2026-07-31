import { Navigate, type RouteObject } from "react-router-dom";
import { PlatformHomePage } from "./pages/PlatformHomePage";
import { TenantReviewDetailPage } from "./pages/TenantReviewDetailPage";
import { TenantReviewListPage } from "./pages/TenantReviewListPage";

export const platformRoutes: { protectedChildren: RouteObject[] } = {
  protectedChildren: [
    { index: true, element: <Navigate to="/platform/home" replace /> },
    { path: "home", element: <PlatformHomePage /> },
    { path: "stores/reviews", element: <TenantReviewListPage /> },
    { path: "stores/reviews/:tenantId", element: <TenantReviewDetailPage /> }
  ]
};
