import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuthStore } from "@/shared/auth/authStore";
import type { UserRole } from "@/shared/constants/roles";

interface ProtectedRouteProps {
  roles: UserRole[];
}

export function ProtectedRoute({ roles }: ProtectedRouteProps) {
  const { currentUser, bootstrapped } = useAuthStore();
  const location = useLocation();

  if (!bootstrapped) {
    return <div className="p-6 text-sm text-ink/70">正在恢复登录状态...</div>;
  }

  if (!currentUser) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (!roles.includes(currentUser.role)) {
    return <Navigate to="/403" replace />;
  }

  return <Outlet />;
}
