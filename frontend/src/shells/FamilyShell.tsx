import { Outlet } from "react-router-dom";
import { Activity, Home, UserRound } from "lucide-react";

export function FamilyShell() {
  return (
    <div className="min-h-screen bg-[#F7F5EF] pb-20">
      <main className="mx-auto max-w-md px-4 py-5">
        <Outlet />
      </main>
      <nav className="fixed inset-x-0 bottom-0 border-t border-ink/10 bg-white">
        <div className="mx-auto grid max-w-md grid-cols-3 text-xs text-ink/65">
          <a className="flex flex-col items-center gap-1 px-3 py-3" href="/family/home">
            <Home size={18} />
            首页
          </a>
          <a className="flex flex-col items-center gap-1 px-3 py-3" href="/family/health-profile">
            <Activity size={18} />
            健康
          </a>
          <a className="flex flex-col items-center gap-1 px-3 py-3" href="/family/mine">
            <UserRound size={18} />
            我的
          </a>
        </div>
      </nav>
    </div>
  );
}
