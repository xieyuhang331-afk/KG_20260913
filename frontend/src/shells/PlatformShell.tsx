import type { ReactNode } from "react";
import {
  BadgeCheck,
  Building2,
  ClipboardCheck,
  ClipboardList,
  HeartPulse,
  LayoutDashboard,
  MailPlus,
  UserRoundCheck,
  ShieldCheck,
  FileCheck2,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { platformNavigation } from "@/domains/platform/navigation";
import { useAuthStore } from "@/shared/auth/authStore";

const navigationIcons: Record<string, ReactNode> = {
  首页: <LayoutDashboard aria-hidden="true" size={17} />,
  入驻审核: <ClipboardCheck aria-hidden="true" size={17} />,
  机构邀请: <MailPlus aria-hidden="true" size={17} />,
  受控入驻审核: <ClipboardList aria-hidden="true" size={17} />,
  健管师资质审核: <UserRoundCheck aria-hidden="true" size={17} />,
  健管师状态: <HeartPulse aria-hidden="true" size={17} />,
  会员实名审核: <BadgeCheck aria-hidden="true" size={17} />,
  同意文档: <FileCheck2 aria-hidden="true" size={17} />,
  组织治理: <Building2 aria-hidden="true" size={17} />,
};

const roleLabels: Record<string, string> = {
  super_admin: "平台管理员",
  province_admin: "省级管理员",
  city_admin: "市级管理员",
};

export function PlatformShell() {
  const { currentUser } = useAuthStore();
  const location = useLocation();
  const visibleNavigation = platformNavigation.filter(
    (item) => !item.roles || (currentUser ? item.roles.includes(currentUser.role) : false),
  );
  const activeItem = visibleNavigation.find(
    (item) => location.pathname === item.path || location.pathname.startsWith(`${item.path}/`),
  );
  const roleLabel = roleLabels[currentUser?.role ?? ""] ?? "平台运营";

  return (
    <div className="min-h-screen bg-[#EEF3F8] text-slate-950">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-16 flex-col border-r border-slate-200 bg-white lg:flex xl:w-64">
        <div className="bg-navy px-4 py-5 text-white">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-teal-500 text-white shadow-lg shadow-black/15">
              <ShieldCheck aria-hidden="true" size={23} />
            </span>
            <div className="hidden xl:block">
              <div className="text-lg font-semibold tracking-wide">康邻智汇</div>
              <div className="text-xs text-slate-300">总平台运营后台</div>
            </div>
          </div>
          <div className="mt-5 hidden rounded-xl border border-white/10 bg-white/[0.07] px-3 py-3 xl:block">
            <div className="text-[11px] font-medium text-slate-300">当前空间</div>
            <div className="mt-1 text-sm font-semibold">平台治理</div>
          </div>
        </div>

        <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-5" aria-label="平台治理主导航">
          <div className="mb-2 hidden px-2 text-[11px] font-semibold tracking-[0.12em] text-slate-400 xl:block">
            主工作区
          </div>
          <div className="space-y-1">
            {visibleNavigation.map((item) => (
              <NavLink
                aria-label={item.label}
                className={({ isActive }) =>
                  [
                    "group flex items-center justify-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors xl:justify-start",
                    isActive
                      ? "bg-teal-50 text-slate-950 ring-1 ring-teal-200"
                      : "text-slate-600 hover:bg-slate-50 hover:text-slate-950",
                  ].join(" ")
                }
                key={item.path}
                to={item.path}
              >
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-50 text-slate-500 ring-1 ring-slate-200 group-aria-[current=page]:bg-white group-aria-[current=page]:text-teal-700">
                  {navigationIcons[item.label]}
                </span>
                <span className="hidden min-w-0 flex-1 truncate xl:block">{item.label}</span>
                <span className="hidden h-1.5 w-1.5 rounded-full bg-teal-500 opacity-0 group-aria-[current=page]:opacity-100 xl:block" />
              </NavLink>
            ))}
          </div>
        </nav>

        <div className="hidden border-t border-slate-200 p-3 xl:block">
          <div className="rounded-xl bg-slate-50 px-3 py-3 text-xs text-slate-500 ring-1 ring-slate-200">
            <div className="flex items-center gap-2 font-semibold text-slate-700">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              真实接口已连接
            </div>
            <div className="mt-1">{roleLabel} · 权限范围由服务端控制</div>
          </div>
        </div>
      </aside>

      <div className="min-w-0 lg:ml-16 xl:ml-64">
        <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur md:px-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-navy text-white lg:hidden">
                <ShieldCheck aria-hidden="true" size={19} />
              </span>
              <div className="min-w-0">
                <div className="truncate text-xs font-medium text-slate-400">
                  平台治理 / {activeItem?.label ?? "工作台"}
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <span className="text-sm font-semibold text-slate-950">平台治理</span>
                  <span className="rounded-full bg-teal-50 px-2.5 py-1 text-xs font-medium text-teal-700">
                    {roleLabel}
                  </span>
                </div>
              </div>
            </div>
            <div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
              <Building2 aria-hidden="true" size={15} />
              总部 / 省 / 市 / 区/县治理空间
            </div>
          </div>
          <nav className="mt-3 flex gap-2 overflow-x-auto pb-1 lg:hidden" aria-label="平台移动导航">
            {visibleNavigation.map((item) => (
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
          <div className="mx-auto w-full max-w-[1280px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
