export const USER_ROLES = {
  member: "member",
  orgAdmin: "org_admin",
  superAdmin: "super_admin",
  provinceAdmin: "province_admin",
  cityAdmin: "city_admin",
  healthExpert: "health_expert",
} as const;

export type UserRole = (typeof USER_ROLES)[keyof typeof USER_ROLES];

export const PLATFORM_REVIEW_ROLES: UserRole[] = [
  USER_ROLES.superAdmin,
  USER_ROLES.provinceAdmin,
  USER_ROLES.cityAdmin,
  USER_ROLES.healthExpert,
];
