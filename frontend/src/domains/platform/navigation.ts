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
  {
    label: "实名审核",
    path: "/platform/identity-reviews",
    roles: [USER_ROLES.superAdmin],
  },
];
