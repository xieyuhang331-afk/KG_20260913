import { useState } from "react";
import { activateInstitution } from "../api";

export function InstitutionActivationPage() {
  const [message, setMessage] = useState("");
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    try {
      await activateInstitution(
        {
          invitation_id: String(values.get("invitation_id") ?? ""),
          phone: String(values.get("phone") ?? ""),
          short_code: String(values.get("short_code") ?? ""),
          password: String(values.get("password") ?? ""),
          totp_secret: String(values.get("totp_secret") ?? ""),
          totp_code: String(values.get("totp_code") ?? ""),
        },
        crypto.randomUUID(),
      );
      setMessage("激活成功，请使用密码与 TOTP 登录");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "激活失败");
    }
  }
  return (
    <section className="w-full max-w-xl rounded-lg bg-white p-6 shadow-sm">
      <h1 className="text-2xl font-semibold">机构邀请激活</h1>
      <p className="mt-2 text-sm text-ink/60">仅接受平台线下发放的邀请 ID 与一次性短码。</p>
      <form className="mt-6 grid gap-3" onSubmit={submit}>
        {[
          ["invitation_id", "邀请 ID"],
          ["phone", "冻结手机号"],
          ["short_code", "6 位短码"],
          ["password", "密码"],
          ["totp_secret", "TOTP 密钥"],
          ["totp_code", "当前 TOTP"],
        ].map(([name, label]) => (
          <label key={name} className="text-sm">
            {label}
            <input
              name={name}
              type={name === "password" ? "password" : "text"}
              required
              className="mt-1 w-full rounded border p-2"
            />
          </label>
        ))}
        <button type="submit" className="rounded bg-pine px-4 py-2 text-white">
          激活
        </button>
      </form>
      {message && (
        <p role="status" className="mt-3 text-sm">
          {message}
        </p>
      )}
    </section>
  );
}
