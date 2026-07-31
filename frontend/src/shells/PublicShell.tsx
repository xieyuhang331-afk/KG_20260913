import { Outlet } from "react-router-dom";

export function PublicShell() {
  return (
    <main className="min-h-screen bg-tea px-4 py-10">
      <div className="mx-auto grid min-h-[calc(100vh-5rem)] max-w-6xl items-center gap-8 lg:grid-cols-[1fr_420px]">
        <section className="hidden lg:block">
          <p className="text-sm font-semibold text-pine">康邻健康管理平台</p>
          <h1 className="mt-4 max-w-2xl text-5xl font-semibold leading-tight text-ink">
            先把真实业务闭环跑起来。
          </h1>
          <p className="mt-5 max-w-xl text-base leading-7 text-ink/70">
            P1 产品化优先交付 Platform Web 与 Institution Web。当前阶段先验证平台审核工作台的登录、鉴权与门店入驻审核闭环。
          </p>
        </section>
        <Outlet />
      </div>
    </main>
  );
}
