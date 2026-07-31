import { Outlet } from "react-router-dom";

export function TherapistShell() {
  return (
    <main className="min-h-screen bg-slate-50 px-4 py-6">
      <div className="mx-auto max-w-4xl">
        <Outlet />
      </div>
    </main>
  );
}
