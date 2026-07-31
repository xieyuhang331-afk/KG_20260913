import { Navigate, type RouteObject } from "react-router-dom";
import type { ReactNode } from "react";
import { PlaceholderPage } from "@/shared/ui/PlaceholderPage";

export const familyRoutes: {
  register: ReactNode;
  protectedChildren: RouteObject[];
} = {
  register: (
    <PlaceholderPage
      title="家庭个人注册"
      description="Phase 1 保留注册入口；下一阶段接入 POST /api/v1/users/register。"
    />
  ),
  protectedChildren: [
    { index: true, element: <Navigate to="/family/home" replace /> },
    {
      path: "home",
      element: <PlaceholderPage title="健康首页" description="下一阶段接入健康摘要、最新指标与数据完整度。" />
    },
    {
      path: "identity",
      element: <PlaceholderPage title="实名认证" description="下一阶段接入实名提交 API。" />
    },
    {
      path: "health-profile/setup",
      element: <PlaceholderPage title="创建健康档案" description="下一阶段接入健康档案创建 API。" />
    },
    {
      path: "health-profile",
      element: <PlaceholderPage title="健康档案" description="下一阶段接入健康档案查询 API。" />
    },
    {
      path: "health/indicators/new",
      element: <PlaceholderPage title="录入健康指标" description="下一阶段接入健康指标写入 API。" />
    },
    {
      path: "health/indicators",
      element: <PlaceholderPage title="健康指标历史" description="下一阶段接入健康指标查询 API。" />
    },
    {
      path: "health/trends",
      element: <PlaceholderPage title="健康趋势" description="下一阶段接入健康趋势 API。" />
    },
    {
      path: "stores",
      element: <PlaceholderPage title="选择服务门店" description="下一阶段接入 active tenant 查询 API。" />
    },
    {
      path: "store-binding",
      element: <PlaceholderPage title="绑定服务门店" description="下一阶段接入用户绑定门店 API。" />
    },
    {
      path: "mine",
      element: <PlaceholderPage title="我的" description="Phase 1 基于 auth/me 预留个人中心入口。" />
    }
  ]
};
