import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  Building2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Landmark,
  Map as MapIcon,
  MapPin,
  Network,
  RotateCw,
  Store,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { isApiError } from "@/shared/api/errors";
import { setCurrentUser, useAuthStore } from "@/shared/auth/authStore";
import { clearAccessToken } from "@/shared/auth/tokenStorage";
import { USER_ROLES } from "@/shared/constants/roles";
import {
  activateOrganization,
  createOrganization,
  deactivateOrganization,
  getOrganizationDetail,
  listOrganizationAdminCandidates,
  listOrganizationTenants,
  listOrganizationTree,
  patchOrganization,
  reorderOrganizationChildren,
} from "../组织基础接口";
import type {
  OrganizationAdminCandidate,
  OrganizationCreateType,
  OrganizationDetail,
  OrganizationTreeNode,
} from "../组织基础类型";

const PAGE_SIZE = 20;

export function PlatformOrganizationPage() {
  const navigate = useNavigate();
  const params = useParams();
  const queryClient = useQueryClient();
  const { currentUser } = useAuthStore();
  const isSuperAdmin = currentUser?.role === USER_ROLES.superAdmin;
  const [includeArchived, setIncludeArchived] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(() => parseOrganizationId(params.organizationId));
  const [includeDescendants, setIncludeDescendants] = useState(true);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<"create" | "edit" | "admin" | "order" | null>(null);
  const [candidates, setCandidates] = useState<OrganizationAdminCandidate[]>([]);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const candidateRequestRef = useRef<AbortController | null>(null);

  useEffect(() => () => candidateRequestRef.current?.abort(), []);

  const treeQuery = useQuery({
    queryKey: ["platform", "organizations", "tree", includeArchived],
    queryFn: () => listOrganizationTree(includeArchived),
    retry: false,
  });

  useEffect(() => {
    if (selectedId === null && treeQuery.data?.length) setSelectedId(treeQuery.data[0].organization_id);
  }, [selectedId, treeQuery.data]);

  const detailQuery = useQuery({
    queryKey: ["platform", "organizations", "detail", selectedId],
    queryFn: () => getOrganizationDetail(selectedId as number),
    enabled: selectedId !== null,
    retry: false,
  });
  const tenantQuery = useQuery({
    queryKey: ["platform", "organizations", "tenants", selectedId, includeDescendants, cursor],
    queryFn: () =>
      listOrganizationTenants(selectedId as number, {
        include_descendants: includeDescendants,
        cursor,
        page_size: PAGE_SIZE,
      }),
    enabled: selectedId !== null,
    retry: false,
  });

  const firstError = treeQuery.error ?? detailQuery.error ?? tenantQuery.error;
  useEffect(() => {
    if (isApiError(firstError) && firstError.status === 401) {
      clearAccessToken();
      setCurrentUser(null);
      navigate("/platform/login", { replace: true });
    }
  }, [firstError, navigate]);

  const selectedNode = useMemo(() => findNode(treeQuery.data ?? [], selectedId), [selectedId, treeQuery.data]);
  const writable = Boolean(
    isSuperAdmin && detailQuery.data?.compatibility_mode === "canonical" && detailQuery.data.status !== "archived",
  );

  function selectNode(id: number) {
    clearCandidateState();
    setSelectedId(id);
    setCursor(null);
    setCursorHistory([]);
    setPanel(null);
    setNotice(null);
    navigate(`/platform/organizations/${id}`, { replace: true });
  }

  async function refreshAfterMutation(message: string) {
    setNotice(message);
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["platform", "organizations", "tree"] }),
      queryClient.invalidateQueries({ queryKey: ["platform", "organizations", "detail", selectedId] }),
      queryClient.invalidateQueries({ queryKey: ["platform", "organizations", "tenants", selectedId] }),
    ]);
  }

  async function runMutation(action: () => Promise<unknown>, successMessage: string) {
    if (busy || !window.confirm("请确认执行本次组织治理操作。")) return;
    setBusy(true);
    setNotice(null);
    try {
      await action();
      setPanel(null);
      clearCandidateState();
      await refreshAfterMutation(successMessage);
    } catch (error) {
      setNotice(getMutationMessage(error));
      if (isApiError(error) && [409, 503].includes(error.status)) await refreshAfterMutation(getMutationMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function openAdminPanel() {
    if (!selectedId || busy) return;
    clearCandidateState();
    const controller = new AbortController();
    candidateRequestRef.current = controller;
    setPanel("admin");
    try {
      const nextCandidates = await listOrganizationAdminCandidates(selectedId, controller.signal);
      if (!controller.signal.aborted && candidateRequestRef.current === controller) setCandidates(nextCandidates);
    } catch (error) {
      if (!controller.signal.aborted && candidateRequestRef.current === controller) {
        setCandidateError(getSafeErrorMessage(error));
      }
    } finally {
      if (candidateRequestRef.current === controller) candidateRequestRef.current = null;
    }
  }

  function clearCandidateState() {
    candidateRequestRef.current?.abort();
    candidateRequestRef.current = null;
    setCandidates([]);
    setCandidateError(null);
  }

  function openGovernancePanel(nextPanel: "create" | "edit" | "order") {
    clearCandidateState();
    setPanel(nextPanel);
  }

  if (treeQuery.isLoading) return <PageMessage>正在加载组织治理树...</PageMessage>;
  if (treeQuery.isError) {
    return <PageError error={treeQuery.error} onRetry={() => void treeQuery.refetch()} />;
  }
  if (!treeQuery.data?.length) {
    return (
      <PageMessage action={<RetryButton onClick={() => void treeQuery.refetch()} />}>
        暂无可访问的组织节点。
      </PageMessage>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-4 py-1">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-slate-400">
            <Network aria-hidden="true" size={14} />
            总部、省、市、区/县四级治理树
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">组织架构管理</h1>
          <p className="mt-1 text-sm text-slate-500">选择组织节点，查看归属门店并执行允许的治理操作。</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-full bg-teal-50 px-3 py-1.5 text-xs font-semibold text-teal-700 ring-1 ring-teal-200">
            真实接口
          </span>
          {isSuperAdmin ? (
            <label className="flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 shadow-sm">
              <input
                checked={includeArchived}
                className="h-4 w-4 accent-teal-600"
                onChange={(event) => {
                  clearCandidateState();
                  setPanel(null);
                  setIncludeArchived(event.target.checked);
                }}
                type="checkbox"
              />
              包含已归档历史节点
            </label>
          ) : null}
        </div>
      </header>

      {notice ? (
        <div
          className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
          role="status"
        >
          <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
          {notice}
        </div>
      ) : null}

      <div className="grid items-start gap-4 min-[1180px]:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel min-[1180px]:sticky min-[1180px]:top-24">
          <div className="border-b border-slate-100 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-950">组织树</h2>
            <p className="mt-0.5 text-xs text-slate-400">总部 / 省 / 市 / 区/县</p>
          </div>
          <nav
            className="max-h-[62vh] space-y-0.5 overflow-y-auto p-3 min-[1180px]:max-h-[calc(100vh-12rem)]"
            aria-label="四级组织树"
          >
            {treeQuery.data.map((node) => (
              <TreeBranch key={node.organization_id} node={node} onSelect={selectNode} selectedId={selectedId} />
            ))}
          </nav>
        </aside>

        <main className="min-w-0 space-y-4">
          {detailQuery.isLoading ? <PageMessage>正在加载组织详情...</PageMessage> : null}
          {detailQuery.isError ? (
            <PageError error={detailQuery.error} onRetry={() => void detailQuery.refetch()} />
          ) : null}
          {detailQuery.data ? (
            <DetailCard
              busy={busy}
              detail={detailQuery.data}
              isSuperAdmin={isSuperAdmin}
              onActivate={() =>
                void runMutation(
                  () =>
                    activateOrganization(detailQuery.data.organization_id, {
                      expected_version: detailQuery.data.version,
                      reason_code: "GOVERNANCE_RESTORED",
                    }),
                  "组织已激活。",
                )
              }
              onCreate={() => openGovernancePanel("create")}
              onDeactivate={() =>
                void runMutation(
                  () =>
                    deactivateOrganization(detailQuery.data.organization_id, {
                      expected_version: detailQuery.data.version,
                      reason_code: "PLATFORM_GOVERNANCE",
                    }),
                  "组织已停用。",
                )
              }
              onEdit={() => openGovernancePanel("edit")}
              onManageAdmin={() => void openAdminPanel()}
              onOrder={() => {
                if (includeArchived) openGovernancePanel("order");
              }}
              orderReady={includeArchived}
              writable={writable}
            />
          ) : null}

          {panel && detailQuery.data ? (
            <GovernancePanel
              busy={busy}
              candidates={candidates}
              candidateError={candidateError}
              detail={detailQuery.data}
              node={selectedNode}
              onCancel={() => {
                clearCandidateState();
                setPanel(null);
              }}
              onRun={runMutation}
              panel={panel}
            />
          ) : null}

          <TenantCard
            cursorHistory={cursorHistory}
            includeDescendants={includeDescendants}
            onIncludeDescendants={(value) => {
              setIncludeDescendants(value);
              setCursor(null);
              setCursorHistory([]);
            }}
            onNext={() => {
              if (!tenantQuery.data?.next_cursor) return;
              setCursorHistory((items) => [...items, cursor]);
              setCursor(tenantQuery.data.next_cursor);
            }}
            onPrevious={() => {
              const previous = cursorHistory[cursorHistory.length - 1];
              if (previous === undefined) return;
              setCursor(previous);
              setCursorHistory((items) => items.slice(0, -1));
            }}
            query={tenantQuery}
          />
        </main>
      </div>
    </div>
  );
}

function TreeBranch({
  node,
  selectedId,
  onSelect,
}: {
  node: OrganizationTreeNode;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const selected = selectedId === node.organization_id;
  return (
    <div>
      <div
        className={`group flex items-center rounded-lg transition-colors ${
          selected ? "bg-teal-50 text-slate-950 ring-1 ring-teal-200" : "text-slate-600 hover:bg-slate-50"
        }`}
      >
        <button
          className="ml-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-white hover:text-slate-700"
          onClick={() => setExpanded((value) => !value)}
          type="button"
          aria-label={`${expanded ? "收起" : "展开"}${node.org_name}`}
        >
          {node.children.length ? (
            expanded ? (
              <ChevronDown size={15} />
            ) : (
              <ChevronRight size={15} />
            )
          ) : (
            <span className="inline-block w-[15px]" />
          )}
        </button>
        <button
          aria-label={node.org_name}
          aria-current={selected ? "true" : undefined}
          className="flex min-w-0 flex-1 items-center gap-2 px-1 py-2.5 pr-2 text-left"
          onClick={() => onSelect(node.organization_id)}
          type="button"
        >
          <span
            className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
              selected ? "bg-white text-teal-700" : "bg-slate-100 text-slate-500"
            }`}
          >
            <OrganizationTypeIcon type={node.org_type} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold">{node.org_name}</span>
            <span className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-400">
              <span>{typeLabel(node.org_type)}</span>
              <StateLabel mode={node.compatibility_mode} status={node.status} />
            </span>
          </span>
        </button>
      </div>
      {expanded && node.children.length ? (
        <div className="ml-5 border-l border-slate-200 pl-2">
          {node.children.map((child) => (
            <TreeBranch key={child.organization_id} node={child} onSelect={onSelect} selectedId={selectedId} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function StateLabel({
  mode,
  status,
}: {
  mode: OrganizationTreeNode["compatibility_mode"];
  status: OrganizationTreeNode["status"];
}) {
  return (
    <>
      {mode === "legacy" ? <span className="text-amber-700">兼容只读</span> : null}
      {status === "inactive" ? <span className="text-amber-700">已停用</span> : null}
      {status === "archived" ? <span className="text-slate-500">已归档</span> : null}
    </>
  );
}

function OrganizationTypeIcon({ type }: { type: OrganizationTreeNode["org_type"] }) {
  if (type === "headquarter" || type === "platform") return <Landmark aria-hidden="true" size={15} />;
  if (type === "province") return <MapIcon aria-hidden="true" size={15} />;
  if (type === "city") return <Building2 aria-hidden="true" size={15} />;
  return <MapPin aria-hidden="true" size={15} />;
}

function DetailCard({
  detail,
  isSuperAdmin,
  writable,
  busy,
  onCreate,
  onEdit,
  onManageAdmin,
  onOrder,
  orderReady,
  onActivate,
  onDeactivate,
}: {
  detail: OrganizationDetail;
  isSuperAdmin: boolean;
  writable: boolean;
  busy: boolean;
  onCreate: () => void;
  onEdit: () => void;
  onManageAdmin: () => void;
  onOrder: () => void;
  orderReady: boolean;
  onActivate: () => void;
  onDeactivate: () => void;
}) {
  const stateMutable = writable && detail.org_type !== "headquarter";
  return (
    <section
      className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel"
      aria-labelledby="selected-organization-title"
    >
      <div className="border-b border-slate-100 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <nav className="flex flex-wrap items-center gap-1.5 text-xs text-slate-400" aria-label="组织层级路径">
              {detail.path_names.map((name, index) => (
                <span className="inline-flex items-center gap-1.5" key={name}>
                  {index ? <ChevronRight aria-hidden="true" size={12} /> : null}
                  <span>{name}</span>
                </span>
              ))}
            </nav>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold tracking-tight text-slate-950" id="selected-organization-title">
                {detail.org_name}
              </h2>
              <StatusBadge status={detail.status} />
              {detail.compatibility_mode === "legacy" ? <ModeBadge /> : null}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
              <span className="font-mono text-[11px]">{detail.org_code}</span>
              <span>{typeLabel(detail.org_type)}</span>
              <span>版本 {detail.version}</span>
            </div>
          </div>
          {isSuperAdmin && writable ? (
            <fieldset className="flex max-w-2xl flex-wrap justify-end gap-2">
              <legend className="sr-only">组织治理操作</legend>
              {detail.org_type !== "county" ? (
                <ActionButton
                  busy={busy}
                  label={detail.org_type === "city" ? "新增区/县" : "创建下级组织"}
                  onClick={onCreate}
                />
              ) : null}
              <ActionButton busy={busy} label="修改组织" onClick={onEdit} />
              {["province", "city"].includes(detail.org_type) && detail.status === "active" ? (
                <ActionButton busy={busy} label="管理员绑定" onClick={onManageAdmin} />
              ) : null}
              <ActionButton busy={busy || !orderReady} label="调整同级排序" onClick={onOrder} />
              {stateMutable && detail.status === "active" ? (
                <ActionButton busy={busy} danger label="停用组织" onClick={onDeactivate} />
              ) : null}
              {stateMutable && detail.status === "inactive" ? (
                <ActionButton busy={busy} label="激活组织" onClick={onActivate} />
              ) : null}
            </fieldset>
          ) : null}
        </div>
      </div>
      {isSuperAdmin && writable && !orderReady ? (
        <p className="mx-5 mt-4 flex items-start gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2.5 text-sm text-blue-800">
          <Archive aria-hidden="true" className="mt-0.5 shrink-0" size={15} />
          排序前请先加载已归档历史节点，确保提交完整的直接下级集合。
        </p>
      ) : null}
      {!writable && isSuperAdmin ? (
        <p className="mx-5 mt-4 rounded-lg bg-slate-50 px-3 py-2.5 text-sm text-slate-600">只读节点不允许治理操作</p>
      ) : null}
      <dl className="grid gap-px bg-slate-100 sm:grid-cols-3 mt-4">
        <Info label="组织类型" value={typeLabel(detail.org_type)} />
        <Info label="状态" value={statusLabel(detail.status)} />
        <Info label="排序" value={String(detail.sort_order)} />
      </dl>
    </section>
  );
}

function GovernancePanel({
  panel,
  detail,
  node,
  candidates,
  candidateError,
  busy,
  onCancel,
  onRun,
}: {
  panel: "create" | "edit" | "admin" | "order";
  detail: OrganizationDetail;
  node?: OrganizationTreeNode;
  candidates: OrganizationAdminCandidate[];
  candidateError: string | null;
  busy: boolean;
  onCancel: () => void;
  onRun: (action: () => Promise<unknown>, message: string) => Promise<void>;
}) {
  const [name, setName] = useState(detail.org_name);
  const [code, setCode] = useState("");
  const [type, setType] = useState<OrganizationCreateType>(nextType(detail.org_type));
  const [adminValue, setAdminValue] = useState(detail.admin_user_id === null ? "" : String(detail.admin_user_id));
  const [ordered, setOrdered] = useState(() => [...(node?.children ?? [])]);

  function move(index: number, delta: number) {
    const target = index + delta;
    if (target < 0 || target >= ordered.length) return;
    const next = [...ordered];
    [next[index], next[target]] = [next[target], next[index]];
    setOrdered(next);
  }

  return (
    <section
      className="rounded-xl border border-teal-200 bg-white p-5 shadow-panel"
      aria-labelledby="governance-panel-title"
    >
      <div className="flex items-center justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.12em] text-teal-700">治理操作</div>
          <h2 className="mt-1 font-semibold text-slate-950" id="governance-panel-title">
            {panelTitle(panel)}
          </h2>
        </div>
        <button
          className="rounded-lg px-3 py-2 text-sm text-slate-500 hover:bg-slate-50 hover:text-slate-950"
          disabled={busy}
          onClick={onCancel}
          type="button"
        >
          关闭
        </button>
      </div>
      {panel === "create" ? (
        <form
          className="mt-4 grid gap-4 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void onRun(
              () =>
                createOrganization({
                  org_name: name,
                  org_code: code,
                  org_type: type,
                  parent_id: detail.organization_id,
                  parent_expected_version: detail.version,
                }),
              "下级组织已创建。",
            );
          }}
        >
          <Field label="组织名称" value={name} onChange={setName} />
          <Field label="组织编码" value={code} onChange={setCode} />
          <label className="text-sm text-slate-600">
            组织类型
            <select
              className="mt-1.5 block min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-950"
              value={type}
              onChange={(event) => setType(event.target.value as OrganizationCreateType)}
            >
              <option value={nextType(detail.org_type)}>{typeLabel(nextType(detail.org_type))}</option>
            </select>
          </label>
          <Submit busy={busy} label="确认创建" />
        </form>
      ) : null}
      {panel === "edit" ? (
        <form
          className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            void onRun(
              () => patchOrganization(detail.organization_id, { org_name: name, expected_version: detail.version }),
              "组织名称已更新。",
            );
          }}
        >
          <Field label="组织名称" value={name} onChange={setName} />
          <Submit busy={busy} label="确认修改" />
        </form>
      ) : null}
      {panel === "admin" ? (
        <form
          className="mt-4 space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void onRun(
              () =>
                patchOrganization(detail.organization_id, {
                  admin_user_id: adminValue ? Number(adminValue) : null,
                  expected_version: detail.version,
                }),
              adminValue ? "管理员已绑定。" : "管理员已解绑。",
            );
          }}
        >
          {candidateError ? <p className="text-sm text-red-700">{candidateError}</p> : null}
          <label className="text-sm text-slate-600">
            管理员候选
            <select
              className="mt-1.5 block min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-950"
              value={adminValue}
              onChange={(event) => setAdminValue(event.target.value)}
            >
              <option value="">不绑定管理员</option>
              {candidates.map((candidate) => (
                <option key={candidate.user_id} value={candidate.user_id}>
                  {candidate.display_name} · {candidate.role}
                </option>
              ))}
            </select>
          </label>
          <Submit busy={busy} label={adminValue ? "确认绑定" : "确认解绑"} />
        </form>
      ) : null}
      {panel === "order" ? (
        <div className="mt-4 space-y-2">
          {ordered.length ? (
            ordered.map((child, index) => (
              <div
                className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5"
                key={child.organization_id}
              >
                <span>{child.org_name}</span>
                <div className="flex gap-2">
                  <button
                    className="rounded-md px-2 py-1 text-sm text-primary-600 hover:bg-white disabled:text-slate-300"
                    disabled={busy || index === 0}
                    onClick={() => move(index, -1)}
                    type="button"
                  >
                    上移
                  </button>
                  <button
                    className="rounded-md px-2 py-1 text-sm text-primary-600 hover:bg-white disabled:text-slate-300"
                    disabled={busy || index === ordered.length - 1}
                    onClick={() => move(index, 1)}
                    type="button"
                  >
                    下移
                  </button>
                </div>
              </div>
            ))
          ) : (
            <p className="text-sm text-slate-500">当前节点没有可排序的直接下级。</p>
          )}
          <button
            className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={busy || ordered.length === 0}
            onClick={() =>
              void onRun(
                () =>
                  reorderOrganizationChildren(detail.organization_id, {
                    parent_expected_version: detail.version,
                    items: ordered.map((child, index) => ({
                      organization_id: child.organization_id,
                      expected_version: child.version,
                      sort_order: index,
                    })),
                  }),
                "同级排序已更新。",
              )
            }
            type="button"
          >
            确认排序
          </button>
        </div>
      ) : null}
    </section>
  );
}

function TenantCard({
  query,
  includeDescendants,
  onIncludeDescendants,
  cursorHistory,
  onPrevious,
  onNext,
}: {
  query: ReturnType<typeof useQuery<Awaited<ReturnType<typeof listOrganizationTenants>>>>;
  includeDescendants: boolean;
  onIncludeDescendants: (value: boolean) => void;
  cursorHistory: Array<string | null>;
  onPrevious: () => void;
  onNext: () => void;
}) {
  return (
    <section
      className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel"
      aria-labelledby="tenant-list-title"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div>
          <div className="flex items-center gap-2">
            <Store aria-hidden="true" className="text-teal-700" size={17} />
            <h2 className="font-semibold text-slate-950" id="tenant-list-title">
              归属门店
            </h2>
            {query.data ? (
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">
                当前批次 {query.data.items.length} 家
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-xs text-slate-500">按游标分批读取；上一批/下一批不虚构总页数。</p>
        </div>
        <label className="flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-sm text-slate-600">
          <input
            checked={includeDescendants}
            className="h-4 w-4 accent-teal-600"
            onChange={(event) => onIncludeDescendants(event.target.checked)}
            type="checkbox"
          />
          包含后代节点
        </label>
      </div>
      {query.isLoading ? <PageMessage>正在加载关联门店...</PageMessage> : null}
      {query.isError ? <PageError error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.data?.items.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">机构名称</th>
                <th className="px-4 py-3 font-medium">类型 / 等级</th>
                <th className="px-4 py-3">区域</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3">组织路径</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((item) => (
                <tr className="border-t border-slate-100 transition-colors hover:bg-slate-50/80" key={item.tenant_id}>
                  <td className="px-4 py-3">
                    <div className="font-semibold text-slate-950">{item.name}</div>
                    <div className="mt-0.5 font-mono text-[11px] text-slate-400">{item.tenant_code}</div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1.5">
                      <span className="rounded-full bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700">
                        {item.type}
                      </span>
                      {item.grade ? <GradeBadge grade={item.grade} /> : <span className="text-slate-400">-</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {[item.province, item.city, item.district].filter(Boolean).join(" / ") || "-"}
                  </td>
                  <td className="px-4 py-3">
                    <TenantStatusBadge status={item.status} />
                  </td>
                  <td className="px-4 py-3 font-mono text-[11px] text-slate-500">
                    {item.organization_path_codes.join(" / ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : query.isSuccess ? (
        <PageMessage>当前范围暂无关联门店。</PageMessage>
      ) : null}
      <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-3">
        <span className="text-xs text-slate-400">游标分页</span>
        <div className="flex gap-2">
          <button
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:border-teal-200 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={cursorHistory.length === 0}
            onClick={onPrevious}
            type="button"
          >
            上一批
          </button>
          <button
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 hover:border-teal-200 hover:text-teal-700 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={!query.data?.next_cursor}
            onClick={onNext}
            type="button"
          >
            下一批
          </button>
        </div>
      </div>
    </section>
  );
}

function PageError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  if (isApiError(error) && error.status === 401) return <PageMessage>登录状态已失效，正在安全退出...</PageMessage>;
  return (
    <div className="rounded-xl border border-red-200 bg-white p-8 text-center shadow-panel" role="alert">
      <CircleAlert aria-hidden="true" className="mx-auto text-red-600" size={24} />
      <p className="mt-3 text-sm font-medium text-red-700">{getSafeErrorMessage(error)}</p>
      <p className="mt-1 text-xs text-slate-500">可重试读取；治理操作不会自动重放。</p>
      <RetryButton onClick={onRetry} />
    </div>
  );
}

function PageMessage({ children, action }: { children: string; action?: React.ReactNode }) {
  return (
    <div
      className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500 shadow-panel"
      role="status"
    >
      <Network aria-hidden="true" className="mx-auto mb-3 text-slate-300" size={24} />
      <p>{children}</p>
      {action}
    </div>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      className="mx-auto mt-4 block rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-teal-200 hover:text-teal-700"
      onClick={onClick}
      type="button"
    >
      <RotateCw className="mr-2 inline" size={14} />
      重试
    </button>
  );
}

function ActionButton({
  label,
  busy,
  danger = false,
  onClick,
}: {
  label: string;
  busy: boolean;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`rounded-lg border bg-white px-3 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        danger
          ? "border-red-200 text-red-700 hover:bg-red-50"
          : "border-slate-200 text-slate-700 hover:border-teal-200 hover:text-teal-700"
      }`}
      disabled={busy}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  );
}

function Submit({ label, busy }: { label: string; busy: boolean }) {
  return (
    <button
      className="self-end rounded-lg bg-teal-600 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-700 disabled:cursor-not-allowed disabled:opacity-40"
      disabled={busy}
      type="submit"
    >
      {busy ? "提交中..." : label}
    </button>
  );
}

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="text-sm text-slate-600">
      {label}
      <input
        className="mt-1.5 block min-h-10 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-slate-950"
        onChange={(event) => onChange(event.target.value)}
        required
        value={value}
      />
    </label>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white px-5 py-4">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 text-sm font-semibold text-slate-950">{value}</dd>
    </div>
  );
}

function StatusBadge({ status }: { status: OrganizationDetail["status"] }) {
  const tone = {
    active: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    inactive: "bg-amber-50 text-amber-700 ring-amber-200",
    archived: "bg-slate-100 text-slate-600 ring-slate-200",
    disabled: "bg-slate-100 text-slate-600 ring-slate-200",
  }[status];
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${tone}`}>{statusLabel(status)}</span>;
}

function ModeBadge() {
  return (
    <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-amber-200">
      兼容只读
    </span>
  );
}

function GradeBadge({ grade }: { grade: string }) {
  const normalized = grade.toLowerCase();
  const tone =
    normalized.includes("flag") || normalized === "a"
      ? "bg-violet-50 text-violet-700 ring-violet-200"
      : normalized.includes("standard") || normalized === "b"
        ? "bg-blue-50 text-blue-700 ring-blue-200"
        : "bg-emerald-50 text-emerald-700 ring-emerald-200";
  return <span className={`rounded-full px-2 py-1 text-xs font-medium ring-1 ${tone}`}>{grade}</span>;
}

function TenantStatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const active = normalized === "active" || normalized === "normal";
  const paused = normalized === "inactive" || normalized === "paused";
  const tone = active
    ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
    : paused
      ? "bg-amber-50 text-amber-700 ring-amber-200"
      : "bg-slate-100 text-slate-600 ring-slate-200";
  const label = active ? "正常" : paused ? "暂停" : status;
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ${tone}`}>{label}</span>;
}

function findNode(nodes: OrganizationTreeNode[], id: number | null): OrganizationTreeNode | undefined {
  for (const node of nodes) {
    if (node.organization_id === id) return node;
    const child = findNode(node.children, id);
    if (child) return child;
  }
  return undefined;
}

function parseOrganizationId(value?: string) {
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

function nextType(type: OrganizationDetail["org_type"]): OrganizationCreateType {
  if (type === "headquarter") return "province";
  if (type === "province") return "city";
  return "county";
}

function typeLabel(type: OrganizationDetail["org_type"]) {
  return (
    {
      headquarter: "总部",
      province: "省",
      city: "市",
      county: "区/县",
      platform: "历史平台组织",
      tenant_org: "历史机构组织",
    } as const
  )[type];
}

function statusLabel(status: OrganizationDetail["status"]) {
  return ({ active: "已激活", inactive: "已停用", archived: "已归档", disabled: "历史停用" } as const)[status];
}

function panelTitle(panel: "create" | "edit" | "admin" | "order") {
  return (
    { create: "创建下级组织", edit: "修改组织", admin: "管理员绑定", order: "同父节点完整 children 原子排序" } as const
  )[panel];
}

function getSafeErrorMessage(error: unknown) {
  if (!isApiError(error)) return "组织信息加载失败，请稍后重试。";
  if (error.status === 400) return "组织分页游标已失效，请刷新后重试。";
  if (error.status === 403) return "当前账号无权访问组织信息。";
  if (error.status === 404) return "组织信息不存在或不在当前访问范围。";
  if (error.status === 422) return "组织请求字段或层级不符合合同，请检查后重试。";
  if (error.status === 503) return "组织服务暂时不可用，请稍后重试。";
  return "组织信息加载失败，请稍后重试。";
}

function getMutationMessage(error: unknown) {
  const code = getOrganizationErrorCode(error);
  if (code === "ORGANIZATION_CODE_CONFLICT") return "组织编码已存在，请更换编码后重试。";
  if (code === "ORGANIZATION_PARENT_INVALID") return "父节点状态或组织层级已变化，已刷新最新数据。";
  if (code === "ORGANIZATION_STATE_CONFLICT") return "组织状态已变化，已刷新最新数据。";
  if (code === "ORGANIZATION_ADMIN_CONFLICT") return "管理员候选资格或绑定状态已变化，请刷新后重新选择。";
  if (code === "ORGANIZATION_REPARENT_NOT_OPEN") return "跨父节点迁移未开放，本次操作未执行。";
  if (code === "ORGANIZATION_COMPATIBILITY_READ_ONLY" || code === "ORGANIZATION_LIFECYCLE_NOT_OPEN") {
    return "该历史组织为只读状态，不允许治理操作。";
  }
  if (code === "ORGANIZATION_COMMIT_OUTCOME_UNKNOWN") {
    return "提交结果未知，已刷新数据；请核对最新状态，不会自动重放。";
  }
  if (
    code === "ORGANIZATION_VERSION_CONFLICT" ||
    code === "ORGANIZATION_ORDER_SET_CONFLICT" ||
    (isApiError(error) && error.status === 409)
  ) {
    return "组织版本或状态已变化，已刷新最新数据，请确认后重试。";
  }
  if (isApiError(error) && error.status === 422) return "父节点、组织层级或请求字段无效，请检查后重试。";
  if (isApiError(error) && error.status === 503)
    return "提交结果未知或服务暂不可用，已刷新数据；请核对状态，不会自动重放。";
  return "组织治理操作失败，请稍后重试。";
}

function getOrganizationErrorCode(error: unknown) {
  if (!isApiError(error) || !error.payload || typeof error.payload !== "object") return null;
  const detail = "detail" in error.payload ? (error.payload as { detail: unknown }).detail : null;
  return typeof detail === "string" && detail.startsWith("ORGANIZATION_") ? detail : null;
}
