import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { BadgeCheck, Building2, ClipboardCheck, LayoutDashboard } from "lucide-react";
import { platformNavigation } from "@/domains/platform/navigation";
import { useAuthStore } from "@/shared/auth/authStore";

const navigationIcons: Record<string, ReactNode> = {
  首页: <LayoutDashboard size={16} />,
  入驻审核: <ClipboardCheck size={16} />,
  实名审核: <BadgeCheck size={16} />,
  组织治理: <Building2 size={16} />,
};

export function PlatformShell() {
  const { currentUser } = useAuthStore();

  return (
    <div className="min-h-screen bg-slate-50">
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-slate-200 bg-white px-5 py-6 md:block">
        <div className="flex items-center gap-3 text-pine">
          <Building2 size={24} />
          <div>
            <span className="block font-semibold">总平台 Web</span>
            <span className="text-xs text-slate-500">Platform Console</span>
          </div>
        </div>
        <nav className="mt-8 space-y-2 text-sm">
          {platformNavigation
            .filter((item) => !item.roles || (currentUser ? item.roles.includes(currentUser.role) : false))
            .map((item) => (
              <NavLink
                className={({ isActive }) =>
                  [
                    "flex items-center gap-2 rounded-md px-3 py-2 font-medium",
                    isActive ? "bg-mint text-pine" : "text-slate-600 hover:bg-slate-50 hover:text-ink",
                  ].join(" ")
                }
                key={item.path}
                to={item.path}
              >
                {navigationIcons[item.label]}
                {item.label}
              </NavLink>
            ))}
        </nav>
        <div className="absolute bottom-6 left-5 right-5 rounded-md bg-slate-50 p-3 text-xs text-slate-500">
          <div className="font-medium text-ink">当前账号</div>
          <div className="mt-1">ID：{currentUser?.id ?? "-"}</div>
          <div className="mt-1">角色：{currentUser?.role ?? "-"}</div>
        </div>
      </aside>
      <main className="min-h-screen px-4 py-6 md:ml-64 md:px-8">
        <Outlet />
      </main>
    </div>
  );
}
