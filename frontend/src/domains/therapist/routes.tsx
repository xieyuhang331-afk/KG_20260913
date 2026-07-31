import { type RouteObject } from "react-router-dom";
import { PlaceholderPage } from "@/shared/ui/PlaceholderPage";

export const therapistRoutes: { protectedChildren: RouteObject[] } = {
  protectedChildren: [
    {
      index: true,
      element: <PlaceholderPage title="健管师端预留" description="P1 不开发健管师业务页，等待服务履约 API 完成。" />
    }
  ]
};
