import brandLogo from "@/assets/康邻智汇LOGO.png";

interface BrandLogoProps {
	compact?: boolean;
	subtitle: string;
	tone?: "dark" | "light";
}

export function BrandLogo({
	compact = false,
	subtitle,
	tone = "light",
}: BrandLogoProps) {
	return (
		<div className="flex min-w-0 items-center gap-3">
			<img
				alt="康邻智汇"
				className={`${
					compact
						? "h-9 w-9 object-cover object-left"
						: "h-9 w-9 object-cover object-left xl:h-10 xl:w-[154px] xl:object-contain"
				} ${tone === "dark" ? "rounded-md bg-white/95 p-1" : ""} shrink-0`}
				src={brandLogo}
			/>
			{!compact ? (
				<span
					className={`hidden border-l pl-3 text-xs xl:block ${tone === "dark" ? "border-white/15 text-slate-300" : "border-slate-200 text-slate-500"}`}
				>
					{subtitle}
				</span>
			) : null}
		</div>
	);
}
