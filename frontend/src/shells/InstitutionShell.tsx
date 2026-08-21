import { Building2, ClipboardList, MailPlus, Store, StoreIcon, UsersRound } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { institutionNavigation } from "@/domains/institution/navigation";
import { useAuthStore } from "@/shared/auth/authStore";

export function InstitutionShell() {
  const { currentUser } = useAuthStore();
  const location = useLocation();
  const activeItem = institutionNavigation.find(
    (item) => location.pathname === item.path || location.pathname.startsWith(`${item.path}/`),
  );

  return (
    <div className="min-h-screen bg-[#EEF3F8] text-slate-950">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-slate-200 bg-white lg:flex">
        <div className="bg-navy px-4 py-5 text-white">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-teal-500 text-white shadow-lg shadow-black/15">
              <Store aria-hidden="true" size={22} />
            </span>
            <div>
              <div className="text-lg font-semibold tracking-wide">康邻智汇</div>
              <div className="text-xs text-slate-300">机构端运营后台</div>
            </div>
          </div>
          <div className="mt-5 rounded-xl border border-white/10 bg-white/[0.07] px-3 py-3">
            <div className="text-[11px] font-medium text-slate-300">当前空间</div>
            <div className="mt-1 text-sm font-semibold">门店运营</div>
          </div>
        </div>

        <nav className="min-h-0 flex-1 px-3 py-5" aria-label="机构端主导航">
          <div className="mb-2 px-2 text-[11px] font-semibold tracking-[0.12em] text-slate-400">机构工作区</div>
          <div className="space-y-1">
            {institutionNavigation.map((item) => (
              <NavLink
                className={({ isActive }) =>
                  [
                    "group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-teal-50 text-slate-950 ring-1 ring-teal-200"
                      : "text-slate-600 hover:bg-slate-50 hover:text-slate-950",
                  ].join(" ")
                }
                key={item.path}
                to={item.path}
              >
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-50 text-slate-500 ring-1 ring-slate-200 group-aria-[current=page]:bg-white group-aria-[current=page]:text-teal-700">
                  {item.label === "组织资料" ? (
                    <Building2 aria-hidden="true" size={17} />
                  ) : item.label === "会员邀请" ? (
                    <MailPlus aria-hidden="true" size={17} />
                  ) : item.label === "会员入组" ? (
                    <UsersRound aria-hidden="true" size={17} />
                  ) : (
                    <ClipboardList aria-hidden="true" size={17} />
                  )}
                </span>
                <span className="min-w-0 flex-1 truncate">{item.label}</span>
                <span className="h-1.5 w-1.5 rounded-full bg-teal-500 opacity-0 group-aria-[current=page]:opacity-100" />
              </NavLink>
            ))}
          </div>
        </nav>

        <div className="border-t border-slate-200 p-3">
          <div className="rounded-xl bg-slate-50 px-3 py-3 text-xs text-slate-500 ring-1 ring-slate-200">
            <div className="flex items-center gap-2 font-semibold text-slate-700">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              机构资料只读展示
            </div>
            <div className="mt-1">组织归属由平台治理，当前账号仅查看本店</div>
          </div>
        </div>
      </aside>

      <div className="min-w-0 lg:ml-64">
        <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur md:px-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-navy text-white lg:hidden">
                <StoreIcon aria-hidden="true" size={19} />
              </span>
              <div className="min-w-0">
                <div className="truncate text-xs font-medium text-slate-400">
                  门店运营 / {activeItem?.label ?? "机构工作台"}
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <span className="text-sm font-semibold text-slate-950">机构运营</span>
                  <span className="rounded-full bg-teal-50 px-2.5 py-1 text-xs font-medium text-teal-700">
                    机构管理员
                  </span>
                </div>
              </div>
            </div>
            <div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
              <Store aria-hidden="true" size={15} />
              门店主体 #{currentUser?.tenant_id ?? "-"}
            </div>
          </div>
          <nav className="mt-3 flex gap-2 overflow-x-auto pb-1 lg:hidden" aria-label="机构移动导航">
            {institutionNavigation.map((item) => (
              <NavLink
                className={({ isActive }) =>
                  `shrink-0 rounded-lg px-3 py-2 text-sm font-medium ${
                    isActive ? "bg-teal-50 text-teal-700 ring-1 ring-teal-200" : "text-slate-600"
                  }`
                }
                key={item.path}
                to={item.path}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </header>
        <main className="min-h-[calc(100vh-65px)] px-4 py-5 md:px-6">
          <div className="mx-auto w-full max-w-[1440px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
