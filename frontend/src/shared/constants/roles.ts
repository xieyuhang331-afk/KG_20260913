export const USER_ROLES = {
  member: "member",
  orgAdmin: "org_admin",
  orgOperator: "org_operator",
  superAdmin: "super_admin",
  sysAdmin: "sys_admin",
  provinceAdmin: "province_admin",
  cityAdmin: "city_admin",
  expert: "expert",
} as const;

export type UserRole = (typeof USER_ROLES)[keyof typeof USER_ROLES];

export const PLATFORM_REVIEW_ROLES: UserRole[] = [
  USER_ROLES.superAdmin,
  USER_ROLES.provinceAdmin,
  USER_ROLES.cityAdmin,
  USER_ROLES.sysAdmin,
  USER_ROLES.expert,
];
