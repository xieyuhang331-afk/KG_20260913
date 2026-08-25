import type { UserRole } from "@/shared/constants/roles";
import { USER_ROLES } from "@/shared/constants/roles";

interface PlatformNavigationItem {
  label: string;
  path: string;
  roles?: UserRole[];
}

export const platformNavigation: PlatformNavigationItem[] = [
  { label: "首页", path: "/platform/home" },
  { label: "入驻审核", path: "/platform/stores/reviews" },
  { label: "机构邀请", path: "/platform/institution-invitations", roles: [USER_ROLES.superAdmin] },
  { label: "受控入驻审核", path: "/platform/institution-reviews", roles: [USER_ROLES.superAdmin] },
  { label: "健管师资质审核", path: "/platform/therapist-reviews", roles: [USER_ROLES.superAdmin] },
  { label: "健管师状态", path: "/platform/therapist-status", roles: [USER_ROLES.superAdmin] },
  {
    label: "组织治理",
    path: "/platform/organizations",
    roles: [USER_ROLES.superAdmin, USER_ROLES.provinceAdmin, USER_ROLES.cityAdmin],
  },
  {
    label: "用户实名审核",
    path: "/platform/identity-reviews",
    roles: [USER_ROLES.superAdmin],
  },
  { label: "同意文档", path: "/platform/consent-documents", roles: [USER_ROLES.superAdmin] },
  { label: "方案模板治理", path: "/platform/health-plan-templates", roles: [USER_ROLES.superAdmin] },
];

export const healthExpertNavigation: PlatformNavigationItem[] = [
  { label: "健康方案审核", path: "/platform/health-plan-reviews", roles: [USER_ROLES.healthExpert] },
];

export const allPlatformNavigation: PlatformNavigationItem[] = [...platformNavigation, ...healthExpertNavigation];
