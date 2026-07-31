import type { TenantApplicationStatus } from "./types";

export function applicationStatusLabel(status: TenantApplicationStatus) {
  if (status === "pending") return "审核中";
  if (status === "active") return "已通过";
  return "已驳回";
}

export function applicationStatusClassName(status: TenantApplicationStatus) {
  if (status === "pending") return "bg-amber-50 text-amber-700 ring-amber-200";
  if (status === "active") return "bg-emerald-50 text-emerald-700 ring-emerald-200";
  return "bg-red-50 text-red-700 ring-red-200";
}
