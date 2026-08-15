import { useCallback, useEffect, useState } from "react";
import {
  createInstitutionInvitation,
  listInstitutionInvitations,
  resendInstitutionInvitation,
  revokeInstitutionInvitation,
  type InstitutionInvitationPayload,
  type InstitutionInvitationView,
} from "../api";

export function InstitutionInvitationPage() {
  const [rows, setRows] = useState<InstitutionInvitationView[]>([]);
  const [message, setMessage] = useState("");
  const load = useCallback(() => listInstitutionInvitations().then(setRows), []);
  useEffect(() => {
    void load();
  }, [load]);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const raw = new FormData(event.currentTarget);
    const payload: InstitutionInvitationPayload = {
      institution_name: String(raw.get("institution_name") ?? ""),
      institution_type: String(raw.get("institution_type") ?? "") as InstitutionInvitationPayload["institution_type"],
      applicant_phone: String(raw.get("applicant_phone") ?? ""),
      pilot_batch_code: String(raw.get("pilot_batch_code") ?? ""),
      administrative_region_id: Number(raw.get("administrative_region_id")),
      expires_in_minutes: 60,
    };
    try {
      const value = await createInstitutionInvitation(payload, crypto.randomUUID());
      setMessage(`邀请已创建，线下短码：${String(value.short_code)}`);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建失败");
    }
  }
  async function mutateInvitation(row: InstitutionInvitationView, operation: "resend" | "revoke") {
    try {
      const value =
        operation === "resend"
          ? await resendInstitutionInvitation(row.invitation_id, row.version, crypto.randomUUID())
          : await revokeInstitutionInvitation(row.invitation_id, row.version, crypto.randomUUID());
      if (operation === "resend") setMessage(`邀请已重发，线下短码：${String(value.short_code)}`);
      else setMessage("邀请已撤销");
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "邀请操作失败");
    }
  }
  return (
    <main>
      <h1 className="text-2xl font-semibold">机构邀请</h1>
      <form className="mt-4 grid gap-2 md:grid-cols-2" onSubmit={submit}>
        <input name="institution_name" placeholder="机构名称" required className="rounded border p-2" />
        <select name="institution_type" className="rounded border p-2">
          <option value="HEALTH_STORE">健康门店</option>
          <option value="LICENSED_CLINIC">持证诊所</option>
        </select>
        <input name="applicant_phone" placeholder="申请人手机号" required className="rounded border p-2" />
        <input name="pilot_batch_code" placeholder="试点批次" required className="rounded border p-2" />
        <input
          name="administrative_region_id"
          type="number"
          placeholder="区县节点 ID"
          required
          className="rounded border p-2"
        />
        <button type="submit" className="rounded bg-pine p-2 text-white">
          创建邀请
        </button>
      </form>
      {message && (
        <p role="status" className="mt-3">
          {message}
        </p>
      )}
      <ul className="mt-6 space-y-2">
        {rows.map((row) => (
          <li key={row.invitation_id} className="rounded border p-3">
            {row.institution_name} · {row.status}
            {row.status === "ISSUED" && (
              <span className="ml-3 inline-flex gap-2">
                <button
                  type="button"
                  onClick={() => void mutateInvitation(row, "resend")}
                  className="rounded border px-2 py-1"
                >
                  重发邀请
                </button>
                <button
                  type="button"
                  onClick={() => void mutateInvitation(row, "revoke")}
                  className="rounded border px-2 py-1"
                >
                  撤销邀请
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
    </main>
  );
}
