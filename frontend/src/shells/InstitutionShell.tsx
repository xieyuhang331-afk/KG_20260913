import { NavLink, Outlet } from "react-router-dom";
import { ClipboardList, Store } from "lucide-react";
import { institutionNavigation } from "@/domains/institution/navigation";
import { useAuthStore } from "@/shared/auth/authStore";

export function InstitutionShell() {
  const { currentUser } = useAuthStore();

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white px-5">
        <div className="mx-auto flex min-h-16 max-w-6xl flex-wrap items-center justify-between gap-3 py-3">
          <div className="flex items-center gap-3 text-pine">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-mint">
              <Store size={20} />
            </span>
            <div>
              <span className="block font-semibold">机构 Web</span>
              <span className="block text-xs text-slate-500">Institution Console</span>
            </div>
          </div>
          <nav className="order-3 flex w-full items-center gap-2 text-sm sm:order-none sm:w-auto">
            {institutionNavigation.map((item) => (
              <NavLink
                className={({ isActive }) =>
                  [
                    "inline-flex items-center gap-2 rounded-md px-3 py-2 font-medium",
                    isActive ? "bg-mint text-pine" : "text-slate-600 hover:bg-slate-50 hover:text-ink"
                  ].join(" ")
                }
                key={item.path}
                to={item.path}
              >
                <ClipboardList size={16} />
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="text-right text-xs text-slate-500">
            <div className="font-medium text-ink">机构管理员 #{currentUser?.id ?? "-"}</div>
            <div className="mt-1">组织 ID：{currentUser?.org_id ?? "-"}</div>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6 sm:py-8">
        <Outlet />
      </main>
    </div>
  );
}
