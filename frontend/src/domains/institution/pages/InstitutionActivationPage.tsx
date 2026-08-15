import { KeyRound, ShieldCheck } from "lucide-react";
import { useRef, useState } from "react";
import { activateInstitution } from "../api";
import { Feedback, fieldClassName, onboardingErrorMessage, primaryButtonClassName } from "../受控入驻界面";

export function InstitutionActivationPage() {
  const formRef = useRef<HTMLFormElement>(null);
  const [message, setMessage] = useState("");
  const [tone, setTone] = useState<"success" | "error">("success");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMessage("");
    setBusy(true);
    const values = new FormData(event.currentTarget);
    try {
      await activateInstitution(
        {
          invitation_id: String(values.get("invitation_id") ?? "").trim(),
          phone: String(values.get("phone") ?? "").trim(),
          short_code: String(values.get("short_code") ?? "").trim(),
          password: String(values.get("password") ?? ""),
          totp_secret: String(values.get("totp_secret") ?? "").trim(),
          totp_code: String(values.get("totp_code") ?? "").trim(),
        },
        crypto.randomUUID(),
      );
      formRef.current?.reset();
      setTone("success");
      setMessage("机构账号已激活。请使用刚设置的密码和动态验证码登录。");
    } catch (error) {
      clearActivationSecrets(formRef.current);
      setTone("error");
      setMessage(onboardingErrorMessage(error, "激活失败，请核对邀请信息后重试。"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-full max-w-3xl">
      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-panel">
        <header className="border-b border-slate-100 bg-navy px-6 py-6 text-white sm:px-8">
          <div className="flex items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-teal-500 text-white">
              <ShieldCheck aria-hidden="true" size={23} />
            </span>
            <div>
              <p className="text-xs font-semibold tracking-[0.16em] text-teal-100">INVITATION ACTIVATION</p>
              <h1 className="mt-1 text-2xl font-semibold">激活机构管理员账号</h1>
            </div>
          </div>
          <p className="mt-4 max-w-2xl text-sm leading-6 text-slate-300">
            使用平台线下交付的邀请 ID 和一次性短码完成激活。动态密钥与密码只用于本次请求，不会写入页面地址或浏览器草稿。
          </p>
        </header>

        <form className="grid gap-5 p-6 sm:grid-cols-2 sm:p-8" onSubmit={submit} ref={formRef}>
          <Field autoComplete="off" label="邀请 ID" name="invitation_id" />
          <Field autoComplete="tel" inputMode="tel" label="冻结手机号" name="phone" pattern="1[3-9][0-9]{9}" />
          <Field
            autoComplete="one-time-code"
            inputMode="numeric"
            label="6 位短码"
            name="short_code"
            pattern="[0-9]{6}"
          />
          <Field autoComplete="new-password" label="密码" minLength={12} name="password" type="password" />
          <Field autoComplete="off" label="TOTP 密钥" minLength={16} name="totp_secret" type="password" />
          <Field
            autoComplete="one-time-code"
            inputMode="numeric"
            label="当前 TOTP"
            name="totp_code"
            pattern="[0-9]{6}"
          />

          <div className="sm:col-span-2">
            <Feedback message={message} tone={tone} />
          </div>
          <div className="flex flex-col gap-3 sm:col-span-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs leading-5 text-slate-500">连续输错或邀请过期时，停止尝试并联系平台重新发送邀请。</p>
            {tone === "success" && message ? (
              <a className={primaryButtonClassName} href="/institution/login">
                前往机构登录
              </a>
            ) : (
              <button className={primaryButtonClassName} disabled={busy} type="submit">
                <KeyRound aria-hidden="true" className="mr-2" size={17} />
                {busy ? "正在激活…" : "确认激活"}
              </button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}

function clearActivationSecrets(form: HTMLFormElement | null) {
  for (const name of ["short_code", "password", "totp_secret", "totp_code"]) {
    const field = form?.elements.namedItem(name);
    if (field instanceof HTMLInputElement) field.value = "";
  }
}

function Field({
  label,
  name,
  type = "text",
  ...props
}: {
  label: string;
  name: string;
  type?: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="text-sm font-medium text-slate-700">
      {label}
      <input className={fieldClassName} name={name} required type={type} {...props} />
    </label>
  );
}
