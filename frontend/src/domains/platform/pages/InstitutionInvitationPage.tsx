import { Building2, Plus, RefreshCw, RotateCw, ShieldX } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { listOrganizationTree } from "@/domains/organization/组织基础接口";
import type { OrganizationTreeNode } from "@/domains/organization/组织基础类型";
import {
  ConfirmDialog,
  EmptyPanel,
  Feedback,
  LoadingPanel,
  fieldClassName,
  onboardingErrorMessage,
  primaryButtonClassName,
  secondaryButtonClassName,
} from "@/domains/institution/受控入驻界面";
import { isApiError } from "@/shared/api/errors";
import {
  createInstitutionInvitation,
  listInstitutionInvitations,
  resendInstitutionInvitation,
  revokeInstitutionInvitation,
  type InstitutionInvitationPayload,
  type InstitutionInvitationView,
} from "../api";

type PendingAction = {
  operation: "resend" | "revoke";
  row: InstitutionInvitationView;
} | null;

export function InstitutionInvitationPage() {
  const formRef = useRef<HTMLFormElement>(null);
  const [rows, setRows] = useState<InstitutionInvitationView[]>([]);
  const [counties, setCounties] = useState<OrganizationTreeNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [tone, setTone] = useState<"success" | "error">("success");
  const [busy, setBusy] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [issuedCode, setIssuedCode] = useState<{
    code: string;
    expiresAt?: string | null;
  } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [invitations, tree] = await Promise.all([listInstitutionInvitations(), listOrganizationTree(false)]);
      setRows(invitations);
      setCounties(flattenActiveCounties(tree));
    } catch (error) {
      setLoadError(onboardingErrorMessage(error, "邀请列表读取失败，请重试。"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setMessage("");
    setIssuedCode(null);
    const raw = new FormData(event.currentTarget);
    const payload: InstitutionInvitationPayload = {
      institution_name: String(raw.get("institution_name") ?? "").trim(),
      institution_type: String(raw.get("institution_type") ?? "") as InstitutionInvitationPayload["institution_type"],
      applicant_phone: String(raw.get("applicant_phone") ?? "").trim(),
      pilot_batch_code: String(raw.get("pilot_batch_code") ?? "").trim(),
      administrative_region_id: Number(raw.get("administrative_region_id")),
      expires_in_minutes: 60,
    };
    try {
      const value = await createInstitutionInvitation(payload, crypto.randomUUID());
      formRef.current?.reset();
      setIssuedCode({
        code: String(value.short_code),
        expiresAt: value.expires_at,
      });
      setTone("success");
      setMessage("邀请已创建。一次性短码只显示在当前页面，请通过批准的线下方式交付。 ");
      await load();
    } catch (error) {
      setTone("error");
      setMessage(onboardingErrorMessage(error, "邀请创建失败，请检查填写内容后重试。"));
    } finally {
      setBusy(false);
    }
  }

  async function confirmMutation() {
    if (!pendingAction || busy) return;
    setBusy(true);
    setMessage("");
    setIssuedCode(null);
    try {
      const { operation, row } = pendingAction;
      const value =
        operation === "resend"
          ? await resendInstitutionInvitation(row.invitation_id, row.version, crypto.randomUUID())
          : await revokeInstitutionInvitation(row.invitation_id, row.version, crypto.randomUUID());
      if (operation === "resend") {
        setIssuedCode({
          code: String(value.short_code),
          expiresAt: value.expires_at,
        });
        setMessage("邀请已重发，旧短码立即失效。新短码只显示在当前页面。 ");
      } else {
        setMessage("邀请已撤销，原短码不能再用于激活。 ");
      }
      setTone("success");
      setPendingAction(null);
      await load();
    } catch (error) {
      setTone("error");
      setMessage(onboardingErrorMessage(error, "邀请操作失败，请刷新后重试。"));
      setPendingAction(null);
      if (isApiError(error) && error.status === 409) await load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="space-y-5">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold tracking-[0.16em] text-teal-700">CONTROLLED ONBOARDING</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">机构邀请</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
            向已确认的试点机构发出限时邀请，并跟踪激活、重发和撤销状态。
          </p>
        </div>
        <button
          className={secondaryButtonClassName}
          disabled={loading || busy}
          onClick={() => void load()}
          type="button"
        >
          <RefreshCw aria-hidden="true" className="mr-2" size={16} />
          刷新列表
        </button>
      </header>

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-panel xl:sticky xl:top-24">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-teal-50 text-teal-700">
              <Plus aria-hidden="true" size={20} />
            </span>
            <div>
              <h2 className="font-semibold text-slate-950">创建机构邀请</h2>
              <p className="mt-0.5 text-xs text-slate-500">有效期固定为 60 分钟</p>
            </div>
          </div>
          <form className="mt-5 grid gap-4" onSubmit={submit} ref={formRef}>
            <Field controlId="institution-name" label="机构名称">
              <input
                className={fieldClassName}
                id="institution-name"
                maxLength={100}
                minLength={2}
                name="institution_name"
                placeholder="机构名称"
                required
              />
            </Field>
            <Field controlId="institution-type" label="机构类型">
              <select className={fieldClassName} id="institution-type" name="institution_type">
                <option value="HEALTH_STORE">健康门店</option>
                <option value="LICENSED_CLINIC">持证诊所</option>
              </select>
            </Field>
            <Field controlId="applicant-phone" hint="仅用于冻结本次邀请，不会展示在邀请列表。" label="受邀手机号">
              <input
                autoComplete="off"
                className={fieldClassName}
                id="applicant-phone"
                inputMode="tel"
                name="applicant_phone"
                pattern="1[3-9][0-9]{9}"
                placeholder="申请人手机号"
                required
              />
            </Field>
            <Field controlId="pilot-batch" label="试点批次">
              <input
                className={fieldClassName}
                id="pilot-batch"
                maxLength={32}
                name="pilot_batch_code"
                placeholder="试点批次"
                required
              />
            </Field>
            <Field
              controlId="administrative-region"
              hint={counties.length ? "仅可选择启用且规范的区/县节点。" : "没有可用区/县节点，请先检查组织治理树。"}
              label="归属区/县"
            >
              <select
                aria-label="归属区/县"
                className={fieldClassName}
                disabled={counties.length === 0}
                id="administrative-region"
                name="administrative_region_id"
                required
              >
                <option value="">请选择区/县</option>
                {counties.map((node) => (
                  <option key={node.organization_id} value={node.organization_id}>
                    {node.org_name}（{node.org_code}）
                  </option>
                ))}
              </select>
            </Field>
            <button className={primaryButtonClassName} disabled={busy || counties.length === 0} type="submit">
              {busy ? "正在创建…" : "创建邀请"}
            </button>
          </form>
        </section>

        <section className="min-w-0 space-y-4">
          <Feedback message={message} tone={tone} />
          {issuedCode ? (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-5" role="status">
              <p className="text-sm font-semibold text-amber-900">一次性短码</p>
              <p className="mt-2 font-mono text-3xl font-semibold tracking-[0.28em] text-slate-950">
                {issuedCode.code}
              </p>
              <p className="mt-2 text-xs text-amber-800">
                关闭或刷新页面后不再显示
                {issuedCode.expiresAt ? `，到期时间：${formatTime(issuedCode.expiresAt)}` : ""}。
              </p>
              <button
                className="mt-3 text-sm font-semibold text-amber-900 underline underline-offset-4"
                onClick={() => setIssuedCode(null)}
                type="button"
              >
                我已完成线下交付，立即隐藏
              </button>
            </div>
          ) : null}

          {loading ? (
            <LoadingPanel label="正在读取机构邀请…" />
          ) : loadError ? (
            <EmptyPanel
              action={
                <button className={secondaryButtonClassName} onClick={() => void load()} type="button">
                  重试
                </button>
              }
              description={loadError}
              title="邀请列表暂时不可用"
            />
          ) : rows.length === 0 ? (
            <EmptyPanel description="还没有机构邀请。填写左侧信息创建第一条限时邀请。" title="暂无邀请记录" />
          ) : (
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-panel">
              <div className="border-b border-slate-100 px-5 py-4">
                <h2 className="font-semibold text-slate-950">邀请记录</h2>
                <p className="mt-1 text-xs text-slate-500">共 {rows.length} 条，手机号和短码不在列表中展示</p>
              </div>
              <ul className="divide-y divide-slate-100">
                {rows.map((row) => (
                  <li
                    className="flex flex-col gap-4 px-5 py-4 sm:flex-row sm:items-center sm:justify-between"
                    key={row.invitation_id}
                  >
                    <div className="flex min-w-0 gap-3">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-600">
                        <Building2 aria-hidden="true" size={18} />
                      </span>
                      <div className="min-w-0">
                        <p className="truncate font-semibold text-slate-950">{row.institution_name}</p>
                        <p className="mt-1 text-xs text-slate-500">
                          {institutionTypeLabel(row.institution_type)} ·{" "}
                          <span className={statusTextClass(row.status)}>{invitationStatusLabel(row.status)}</span>
                        </p>
                      </div>
                    </div>
                    {row.status === "ISSUED" ? (
                      <div className="flex gap-2">
                        <button
                          className={secondaryButtonClassName}
                          disabled={busy}
                          onClick={() => setPendingAction({ operation: "resend", row })}
                          type="button"
                        >
                          <RotateCw aria-hidden="true" className="mr-1.5" size={15} />
                          重发
                        </button>
                        <button
                          className={`${secondaryButtonClassName} text-red-700 hover:border-red-300 hover:text-red-800`}
                          disabled={busy}
                          onClick={() => setPendingAction({ operation: "revoke", row })}
                          type="button"
                        >
                          <ShieldX aria-hidden="true" className="mr-1.5" size={15} />
                          撤销
                        </button>
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>

      {pendingAction ? (
        <ConfirmDialog
          busy={busy}
          confirmLabel={pendingAction.operation === "resend" ? "确认重发" : "确认撤销"}
          description={
            pendingAction.operation === "resend"
              ? "重发后旧短码立即失效，需要重新安全交付新短码。"
              : "撤销后该邀请不能恢复，机构将无法继续激活。"
          }
          destructive={pendingAction.operation === "revoke"}
          onClose={() => setPendingAction(null)}
          onConfirm={() => void confirmMutation()}
          title={
            pendingAction.operation === "resend"
              ? `重发“${pendingAction.row.institution_name}”邀请？`
              : `撤销“${pendingAction.row.institution_name}”邀请？`
          }
        />
      ) : null}
    </main>
  );
}

function Field({
  controlId,
  label,
  hint,
  children,
}: {
  controlId: string;
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="text-sm font-medium text-slate-700" htmlFor={controlId}>
      {label}
      {children}
      {hint ? <span className="mt-1.5 block text-xs font-normal leading-5 text-slate-500">{hint}</span> : null}
    </label>
  );
}

function flattenActiveCounties(nodes: OrganizationTreeNode[]): OrganizationTreeNode[] {
  return nodes.flatMap((node) => [
    ...(node.org_type === "county" && node.status === "active" && node.compatibility_mode === "canonical"
      ? [node]
      : []),
    ...flattenActiveCounties(node.children),
  ]);
}

function invitationStatusLabel(status: string) {
  return { ISSUED: "待激活", ACTIVATED: "已激活", REVOKED: "已撤销" }[status] ?? status;
}

function statusTextClass(status: string) {
  if (status === "ACTIVATED") return "text-emerald-700";
  if (status === "REVOKED") return "text-red-700";
  return "text-amber-700";
}

function institutionTypeLabel(type: string) {
  return type === "LICENSED_CLINIC" ? "持证诊所" : "健康门店";
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "以服务端状态为准"
    : new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "short",
        timeStyle: "short",
      }).format(date);
}
