import { useQuery } from "@tanstack/react-query";
import { Building2, RotateCw } from "lucide-react";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { isApiError } from "@/shared/api/errors";
import { setCurrentUser } from "@/shared/auth/authStore";
import { clearAccessToken } from "@/shared/auth/tokenStorage";
import { getMyOrganization } from "../组织基础接口";

export function InstitutionOrganizationPage() {
  const navigate = useNavigate();
  const profileQuery = useQuery({
    queryKey: ["institution", "organization", "me"],
    queryFn: getMyOrganization,
    retry: false,
  });

  useEffect(() => {
    if (isApiError(profileQuery.error) && profileQuery.error.status === 401) {
      clearAccessToken();
      setCurrentUser(null);
      navigate("/institution/login", { replace: true });
    }
  }, [navigate, profileQuery.error]);

  if (profileQuery.isLoading) return <PageMessage>正在加载机构组织资料...</PageMessage>;
  if (profileQuery.isError) {
    return (
      <PageError
        error={profileQuery.error}
        onRetry={() => {
          void profileQuery.refetch();
        }}
      />
    );
  }

  const profile = profileQuery.data;
  if (!profile) return <PageMessage>暂无机构组织资料。</PageMessage>;

  const unassigned = profile.assignment_status === "unassigned" || profile.organization_id === null;
  return (
    <div className="space-y-5">
      <header className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-pine">Organization Foundation</p>
        <h1 className="mt-2 text-2xl font-semibold text-ink">机构组织资料</h1>
        <p className="mt-2 text-sm text-slate-500">展示当前门店经营主体及其平台治理树归属。</p>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-center gap-3">
          <span className="rounded-lg bg-mint p-2 text-pine">
            <Building2 size={20} />
          </span>
          <div>
            <h2 className="font-semibold text-ink">{profile.tenant_name}</h2>
            <p className="text-sm text-slate-500">{profile.tenant_code}</p>
          </div>
        </div>
        <dl className="mt-6 grid gap-4 sm:grid-cols-2">
          <Info label="tenant 类型" value={profile.tenant_type} />
          <Info label="tenant 状态" value={profile.tenant_status} />
          <Info
            label="兼容模式"
            value={
              profile.compatibility_mode === null
                ? "暂未分配"
                : profile.compatibility_mode === "legacy"
                  ? "兼容只读"
                  : "标准组织"
            }
          />
          <Info label="组织节点" value={unassigned ? "暂未分配平台组织" : String(profile.organization_id)} />
        </dl>
        <div className="mt-5 rounded-lg bg-slate-50 p-4">
          <div className="text-xs font-medium text-slate-500">组织路径</div>
          <div className="mt-2 text-sm text-ink">
            {unassigned || profile.organization_path.length === 0
              ? "暂未分配平台组织"
              : profile.organization_path.map((node) => node.org_name).join(" / ")}
          </div>
        </div>
      </section>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 text-sm font-medium text-ink">{value}</dd>
    </div>
  );
}

function PageMessage({ children }: { children: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-500">
      {children}
    </div>
  );
}

function PageError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (isApiError(error) && error.status === 401) return <PageMessage>登录状态已失效，正在安全退出...</PageMessage>;
  let message = "机构组织资料加载失败，请稍后重试。";
  if (isApiError(error) && error.status === 403) message = "当前账号无权访问组织信息。";
  if (isApiError(error) && error.status === 404) message = "组织信息不存在或不在当前访问范围。";
  if (isApiError(error) && error.status === 503) message = "组织服务暂时不可用，请稍后重试。";
  return (
    <div className="rounded-xl border border-red-200 bg-white p-10 text-center">
      <p className="text-sm text-red-700">{message}</p>
      <button className="mt-4 rounded-md border border-slate-200 px-4 py-2 text-sm" onClick={onRetry} type="button">
        <RotateCw className="mr-2 inline" size={14} />
        重试
      </button>
    </div>
  );
}
