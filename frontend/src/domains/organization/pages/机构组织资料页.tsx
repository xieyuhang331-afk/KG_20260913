import { useQuery } from "@tanstack/react-query";
import {
  Building2,
  ChevronRight,
  CircleAlert,
  Landmark,
  Map as MapIcon,
  MapPin,
  Network,
  RotateCw,
  Store,
} from "lucide-react";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { isApiError } from "@/shared/api/errors";
import { setCurrentUser } from "@/shared/auth/authStore";
import { clearAccessToken } from "@/shared/auth/tokenStorage";
import type { InstitutionOrganizationPathNode, OrganizationStatus, OrganizationType } from "../组织基础类型";
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
  const pathText = profile.organization_path.map((node) => node.org_name).join(" / ");

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-4 py-1">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-slate-400">
            <Network aria-hidden="true" size={14} />
            机构运营 · 组织归属
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">机构组织资料</h1>
          <p className="mt-1 text-sm text-slate-500">查看当前门店经营主体及其在平台治理树中的位置。</p>
        </div>
        <span className="rounded-full bg-teal-50 px-3 py-1.5 text-xs font-semibold text-teal-700 ring-1 ring-teal-200">
          只读资料
        </span>
      </header>

      <section
        className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel"
        aria-labelledby="tenant-profile-title"
      >
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-100 px-5 py-5">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-teal-700 ring-1 ring-teal-200">
              <Store aria-hidden="true" size={22} />
            </span>
            <div className="min-w-0">
              <h2 className="truncate text-lg font-semibold text-slate-950" id="tenant-profile-title">
                {profile.tenant_name}
              </h2>
              <p className="mt-1 font-mono text-xs text-slate-400">{profile.tenant_code}</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <TenantStatusBadge status={profile.tenant_status} />
            {profile.compatibility_mode === "legacy" ? <ModeBadge /> : null}
            {unassigned ? (
              <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-amber-200">
                暂未归属
              </span>
            ) : null}
          </div>
        </div>

        <div className="grid gap-px bg-slate-100 sm:grid-cols-2 lg:grid-cols-4">
          <Info label="tenant 编码" value={profile.tenant_code} mono />
          <Info label="tenant 类型" value={profile.tenant_type} />
          <Info label="tenant 状态" value={profile.tenant_status} />
          <Info
            label="组织模式"
            value={
              profile.compatibility_mode === null
                ? "暂未分配"
                : profile.compatibility_mode === "legacy"
                  ? "兼容只读"
                  : "标准组织"
            }
          />
        </div>
      </section>

      {unassigned || profile.organization_path.length === 0 ? (
        <section
          className="rounded-xl border border-amber-200 bg-white px-6 py-10 text-center shadow-panel"
          role="status"
        >
          <MapPin aria-hidden="true" className="mx-auto text-amber-500" size={28} />
          <h2 className="mt-3 font-semibold text-slate-950">暂未分配平台组织</h2>
          <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-500">
            当前门店尚未进入平台治理树。请联系平台管理员确认组织归属；机构端不会自行创建或调整组织节点。
          </p>
        </section>
      ) : (
        <section
          className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel"
          aria-labelledby="organization-path-title"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="font-semibold text-slate-950" id="organization-path-title">
                平台治理归属
              </h2>
              <p className="mt-1 text-xs text-slate-500">组织层级由平台维护，机构端只读展示。</p>
            </div>
            <span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700 ring-1 ring-blue-200">
              组织节点 #{profile.organization_id}
            </span>
          </div>

          <div className="mt-4 rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-600">
            <span className="mr-2 text-xs font-medium text-slate-400">完整路径</span>
            <span>{pathText}</span>
          </div>

          <ol className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="平台组织路径">
            {profile.organization_path.map((node, index) => (
              <li className="relative" key={node.organization_id}>
                <PathNode node={node} position={index + 1} />
                {index < profile.organization_path.length - 1 ? (
                  <ChevronRight
                    aria-hidden="true"
                    className="absolute -right-3 top-1/2 z-10 hidden -translate-y-1/2 text-slate-300 xl:block"
                    size={18}
                  />
                ) : null}
              </li>
            ))}
          </ol>

          {profile.compatibility_mode === "legacy" ? (
            <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
              当前归属来自历史兼容映射，仅供查看；治理操作由平台端处理。
            </div>
          ) : null}
        </section>
      )}
    </div>
  );
}

function PathNode({ node, position }: { node: InstitutionOrganizationPathNode; position: number }) {
  return (
    <div className="h-full rounded-xl border border-slate-200 bg-white p-4 ring-1 ring-transparent transition-colors hover:border-teal-200 hover:ring-teal-100">
      <div className="flex items-start justify-between gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-50 text-teal-700 ring-1 ring-slate-200">
          <OrganizationIcon type={node.org_type} />
        </span>
        <span className="text-[11px] font-semibold text-slate-300">L{position}</span>
      </div>
      <div className="mt-3 font-semibold text-slate-950">{node.org_name}</div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>{organizationTypeLabel(node.org_type)}</span>
        <OrganizationStatusBadge status={node.status} />
      </div>
      <div className="mt-3 font-mono text-[10px] text-slate-400">{node.org_code}</div>
    </div>
  );
}

function OrganizationIcon({ type }: { type: OrganizationType }) {
  if (type === "headquarter" || type === "platform") return <Landmark aria-hidden="true" size={17} />;
  if (type === "province") return <MapIcon aria-hidden="true" size={17} />;
  if (type === "city") return <Building2 aria-hidden="true" size={17} />;
  return <MapPin aria-hidden="true" size={17} />;
}

function Info({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="bg-white px-5 py-4">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={`mt-1 text-sm font-semibold text-slate-950 ${mono ? "font-mono text-xs" : ""}`}>{value}</dd>
    </div>
  );
}

function PageMessage({ children }: { children: string }) {
  return (
    <div
      className="rounded-xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-500 shadow-panel"
      role="status"
    >
      <Network aria-hidden="true" className="mx-auto mb-3 text-slate-300" size={25} />
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
    <div className="rounded-xl border border-red-200 bg-white p-10 text-center shadow-panel" role="alert">
      <CircleAlert aria-hidden="true" className="mx-auto text-red-600" size={26} />
      <p className="mt-3 text-sm font-medium text-red-700">{message}</p>
      <p className="mt-1 text-xs text-slate-500">检查网络或稍后重试；页面不会显示未确认的数据。</p>
      <button
        className="mt-4 rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-teal-200 hover:text-teal-700"
        onClick={onRetry}
        type="button"
      >
        <RotateCw aria-hidden="true" className="mr-2 inline" size={14} />
        重试
      </button>
    </div>
  );
}

function TenantStatusBadge({ status }: { status: string }) {
  const active = status.toLowerCase() === "active";
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${
        active ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-amber-50 text-amber-700 ring-amber-200"
      }`}
    >
      {active ? "正常" : status}
    </span>
  );
}

function OrganizationStatusBadge({ status }: { status: OrganizationStatus }) {
  const active = status === "active";
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${
        active ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-slate-100 text-slate-600 ring-slate-200"
      }`}
    >
      {active ? "有效" : status === "inactive" ? "停用" : "历史"}
    </span>
  );
}

function ModeBadge() {
  return (
    <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-amber-200">
      兼容只读
    </span>
  );
}

function organizationTypeLabel(type: OrganizationType) {
  return (
    {
      headquarter: "总部",
      province: "省",
      city: "市",
      county: "区/县",
      platform: "历史平台",
      tenant_org: "历史机构",
    } as const
  )[type];
}
