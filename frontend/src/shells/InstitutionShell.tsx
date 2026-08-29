import {
	BadgeCheck,
	Building2,
	ClipboardList,
	HeartPulse,
	Activity,
	ArrowRightLeft,
	MailPlus,
	Store,
	UsersRound,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { institutionNavigation } from "@/domains/institution/navigation";
import { useAuthStore } from "@/shared/auth/authStore";
import { USER_ROLES } from "@/shared/constants/roles";
import { BrandLogo } from "@/shared/ui/BrandLogo";

const navigationIcons = {
	受控入驻: <BadgeCheck aria-hidden="true" size={17} />,
	我的申请: <ClipboardList aria-hidden="true" size={17} />,
	组织资料: <Building2 aria-hidden="true" size={17} />,
	健管师团队: <UsersRound aria-hidden="true" size={17} />,
	服务就绪: <HeartPulse aria-hidden="true" size={17} />,
	客户服务邀约: <MailPlus aria-hidden="true" size={17} />,
	服务客户列表: <UsersRound aria-hidden="true" size={17} />,
	服务履约: <Activity aria-hidden="true" size={17} />,
	转机构接续: <ArrowRightLeft aria-hidden="true" size={17} />,
} as const;

const navigationGroups = [
	{ label: "机构管理", items: ["受控入驻", "我的申请", "组织资料"] },
	{ label: "服务团队", items: ["健管师团队", "服务就绪"] },
	{
		label: "客户服务",
		items: ["客户服务邀约", "服务客户列表", "服务履约", "转机构接续"],
	},
];

export function InstitutionShell() {
	const { currentUser } = useAuthStore();
	const location = useLocation();
	const activeItem = institutionNavigation.find(
		(item) =>
			location.pathname === item.path ||
			location.pathname.startsWith(`${item.path}/`),
	);

	return (
		<div className="min-h-screen bg-[#EEF3F8] text-slate-950">
			<aside className="fixed inset-y-0 left-0 z-30 hidden w-16 flex-col border-r border-slate-200 bg-white lg:flex xl:w-64">
				<div className="border-b border-slate-100 px-3 py-5 xl:px-5">
					<BrandLogo compact={false} subtitle="机构端运营后台" />
					<div className="mt-5 hidden rounded-xl border border-primary-100 bg-primary-50 px-3 py-3 xl:block">
						<div className="text-[11px] font-medium text-primary-600">
							当前空间
						</div>
						<div className="mt-1 text-sm font-semibold text-slate-950">
							门店运营
						</div>
					</div>
				</div>

				<nav
					className="min-h-0 flex-1 overflow-y-auto px-2 py-4 xl:px-3"
					aria-label="机构端主导航"
				>
					{navigationGroups.map((group) => (
						<section className="mb-4" key={group.label}>
							<h2 className="mb-1 hidden px-3 text-[11px] font-semibold tracking-[0.14em] text-slate-400 xl:block">
								{group.label}
							</h2>
							<div className="space-y-1">
								{institutionNavigation
									.filter((item) => group.items.includes(item.label))
									.map((item) => (
										<NavLink
											aria-label={item.label}
											className={({ isActive }) =>
												[
													"group relative flex items-center justify-center gap-3 overflow-hidden rounded-lg px-2 py-2 text-sm font-medium transition-colors xl:justify-start xl:px-3",
													isActive
														? "bg-primary-50 text-primary-600 ring-1 ring-primary-100"
														: "text-slate-600 hover:bg-slate-50 hover:text-slate-950",
												].join(" ")
											}
											key={item.path}
											to={item.path}
										>
											<span className="absolute inset-y-2 left-0 w-[3px] rounded-r bg-teal-500 opacity-0 group-aria-[current=page]:opacity-100" />
											<span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-slate-50 text-slate-500 ring-1 ring-slate-200 group-aria-[current=page]:bg-white group-aria-[current=page]:text-primary-600">
												{
													navigationIcons[
														item.label as keyof typeof navigationIcons
													]
												}
											</span>
											<span className="hidden min-w-0 flex-1 truncate xl:block">
												{item.label}
											</span>
										</NavLink>
									))}
							</div>
						</section>
					))}
				</nav>

				<div className="hidden border-t border-slate-200 p-3 xl:block">
					<div className="rounded-xl bg-slate-50 px-3 py-3 text-xs text-slate-500 ring-1 ring-slate-200">
						<div className="flex items-center gap-2 font-semibold text-slate-700">
							<span className="h-2 w-2 rounded-full bg-emerald-500" />
							机构资料只读展示
						</div>
						<div className="mt-1">组织归属由平台治理，当前账号仅查看本店</div>
					</div>
				</div>
			</aside>

			<div className="min-w-0 lg:ml-16 xl:ml-64">
				<header className="sticky top-0 z-20 min-h-14 border-b border-slate-200 bg-white/95 px-4 py-2.5 backdrop-blur md:px-6">
					<div className="flex flex-wrap items-center justify-between gap-3">
						<div className="flex min-w-0 items-center gap-3">
							<span className="lg:hidden">
								<BrandLogo compact subtitle="机构端运营后台" />
							</span>
							<div className="min-w-0">
								<div className="truncate text-xs font-medium text-slate-400">
									门店运营 / {activeItem?.label ?? "机构工作台"}
								</div>
								<div className="mt-1 flex items-center gap-2">
									<span className="text-sm font-semibold text-slate-950">
										机构运营
									</span>
									<span className="rounded-full bg-teal-50 px-2.5 py-1 text-xs font-medium text-teal-700">
										{currentUser?.role === USER_ROLES.orgOperator
											? "机构运营人员"
											: "机构管理员"}
									</span>
								</div>
							</div>
						</div>
						<div className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
							<Store aria-hidden="true" size={15} />
							当前机构
						</div>
					</div>
					<nav
						className="mt-3 flex gap-2 overflow-x-auto pb-1 lg:hidden"
						aria-label="机构移动导航"
					>
						{institutionNavigation.map((item) => (
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
