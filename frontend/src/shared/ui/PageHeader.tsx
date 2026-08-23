import type { ReactNode } from "react";

interface PageHeaderProps {
	actions?: ReactNode;
	title: string;
	description?: string;
	eyebrow?: string;
}

export function PageHeader({
	actions,
	title,
	description,
	eyebrow = "业务工作台",
}: PageHeaderProps) {
	return (
		<header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
			<div>
				<p className="text-xs font-semibold tracking-[0.14em] text-teal-700">
					{eyebrow}
				</p>
				<h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">
					{title}
				</h1>
				{description ? (
					<p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
						{description}
					</p>
				) : null}
			</div>
			{actions ? <div className="shrink-0">{actions}</div> : null}
		</header>
	);
}
