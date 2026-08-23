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
	FileCheck2,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { platformNavigation } from "@/domains/platform/navigation";
import { useAuthStore } from "@/shared/auth/authStore";
import { BrandLogo } from "@/shared/ui/BrandLogo";

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

const navigationGroups = [
	{ label: "运营总览", items: ["首页"] },
	{
		label: "机构治理",
		items: ["入驻审核", "机构邀请", "受控入驻审核", "组织治理"],
	},
	{ label: "人员服务", items: ["健管师资质审核", "健管师状态"] },
	{ label: "会员合规", items: ["会员实名审核", "同意文档"] },
];

export function PlatformShell() {
	const { currentUser } = useAuthStore();
	const location = useLocation();
	const visibleNavigation = platformNavigation.filter(
		(item) =>
			!item.roles ||
			(currentUser ? item.roles.includes(currentUser.role) : false),
	);
	const activeItem = visibleNavigation.find(
		(item) =>
			location.pathname === item.path ||
			location.pathname.startsWith(`${item.path}/`),
	);
	const roleLabel = roleLabels[currentUser?.role ?? ""] ?? "平台运营";

	return (
		<div className="min-h-screen bg-[#EEF3F8] text-slate-950">
			<aside className="fixed inset-y-0 left-0 z-30 hidden w-16 flex-col border-r border-[#003C8C] bg-navy text-white lg:flex xl:w-64">
				<div className="px-3 py-5 xl:px-5">
					<BrandLogo compact={false} subtitle="总平台运营后台" tone="dark" />
					<div className="mt-5 hidden rounded-xl border border-white/10 bg-white/[0.07] px-3 py-3 xl:block">
						<div className="text-[11px] font-medium text-slate-300">
							当前空间
						</div>
						<div className="mt-1 text-sm font-semibold">平台治理</div>
					</div>
				</div>

				<nav
					className="min-h-0 flex-1 overflow-y-auto px-2 py-4 xl:px-3"
					aria-label="平台治理主导航"
				>
					{navigationGroups.map((group) => {
						const items = visibleNavigation.filter((item) =>
							group.items.includes(item.label),
						);
						if (!items.length) return null;
						return (
							<section className="mb-4" key={group.label}>
								<h2 className="mb-1 hidden px-3 text-[11px] font-semibold tracking-[0.14em] text-slate-400 xl:block">
									{group.label}
								</h2>
								<div className="space-y-1">
									{items.map((item) => (
										<NavLink
											aria-label={item.label}
											className={({ isActive }) =>
												[
													"group relative flex items-center justify-center gap-3 overflow-hidden rounded-lg px-2 py-2 text-sm font-medium transition-colors xl:justify-start xl:px-3",
													isActive
														? "bg-[#004391] text-white shadow-sm"
														: "text-slate-300 hover:bg-white/[0.08] hover:text-white",
												].join(" ")
											}
											key={item.path}
											to={item.path}
										>
											<span className="absolute inset-y-2 left-0 w-[3px] rounded-r bg-teal-400 opacity-0 group-aria-[current=page]:opacity-100" />
											<span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-white/[0.06] text-slate-300 group-aria-[current=page]:bg-white/10 group-aria-[current=page]:text-white">
												{navigationIcons[item.label]}
											</span>
											<span className="hidden min-w-0 flex-1 truncate xl:block">
												{item.label}
											</span>
										</NavLink>
									))}
								</div>
							</section>
						);
					})}
				</nav>

				<div className="hidden border-t border-white/10 p-3 xl:block">
					<div className="rounded-xl bg-white/[0.06] px-3 py-3 text-xs text-slate-300 ring-1 ring-white/10">
						<div className="flex items-center gap-2 font-semibold text-white">
							<span className="h-2 w-2 rounded-full bg-emerald-500" />
							真实接口已连接
						</div>
						<div className="mt-1">{roleLabel} · 权限范围由服务端控制</div>
					</div>
				</div>
			</aside>

			<div className="min-w-0 lg:ml-16 xl:ml-64">
				<header className="sticky top-0 z-20 min-h-14 border-b border-slate-200 bg-white/95 px-4 py-2.5 backdrop-blur md:px-6">
					<div className="flex flex-wrap items-center justify-between gap-3">
						<div className="flex min-w-0 items-center gap-3">
							<span className="lg:hidden">
								<BrandLogo compact subtitle="总平台运营后台" />
							</span>
							<div className="min-w-0">
								<div className="truncate text-xs font-medium text-slate-400">
									平台治理 / {activeItem?.label ?? "工作台"}
								</div>
								<div className="mt-1 flex items-center gap-2">
									<span className="text-sm font-semibold text-slate-950">
										平台治理
									</span>
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
					<nav
						className="mt-3 flex gap-2 overflow-x-auto pb-1 lg:hidden"
						aria-label="平台移动导航"
					>
						{visibleNavigation.map((item) => (
							<NavLink
								className={({ isActive }) =>
									`shrink-0 rounded-lg px-3 py-2 text-sm font-medium ${
										isActive
											? "bg-teal-50 text-teal-700 ring-1 ring-teal-200"
											: "text-slate-600"
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
				<main className="min-h-[calc(100vh-56px)] px-4 py-5 md:px-6 md:py-6">
					<div className="mx-auto w-full max-w-[1280px]">
						<Outlet />
					</div>
				</main>
			</div>
		</div>
	);
}
