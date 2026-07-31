import { zodResolver } from "@hookform/resolvers/zod";
import { LogIn } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";
import { login } from "@/domains/auth/api";
import { setCurrentUser } from "@/shared/auth/authStore";
import { setAccessToken } from "@/shared/auth/tokenStorage";
import { PLATFORM_REVIEW_ROLES, USER_ROLES, type UserRole } from "@/shared/constants/roles";

const loginSchema = z.object({
  phone: z.string().min(1, "请输入手机号或账号"),
  password: z.string().min(1, "请输入密码")
});

type LoginFormValues = z.infer<typeof loginSchema>;

export function LoginPage() {
  const navigate = useNavigate();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting }
  } = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema)
  });

  async function onSubmit(values: LoginFormValues) {
    setErrorMessage(null);
    try {
      const result = await login(values);
      setAccessToken(result.access_token);
      setCurrentUser(result.user);
      navigate(getRoleHomePath(result.user.role), { replace: true });
    } catch (error) {
      const message = error instanceof Error ? error.message : "登录失败";
      setErrorMessage(message);
    }
  }

  return (
    <section className="w-full max-w-md rounded-lg border border-ink/10 bg-white p-6 shadow-sm">
      <div className="mb-6">
        <p className="text-sm font-semibold text-pine">康邻健康管理平台</p>
        <h2 className="mt-2 text-2xl font-semibold text-ink">登录工作台</h2>
        <p className="mt-2 text-sm text-ink/60">使用后端 JWT 登录接口进入对应角色入口。</p>
      </div>
      <form className="space-y-4" onSubmit={handleSubmit(onSubmit)}>
        <label className="block">
          <span className="text-sm font-medium text-ink">手机号或账号</span>
          <input
            className="mt-2 w-full rounded-md border border-ink/15 px-3 py-2 outline-none focus:border-pine focus:ring-2 focus:ring-mint"
            autoComplete="username"
            {...register("phone")}
          />
          {errors.phone ? <span className="mt-1 block text-xs text-coral">{errors.phone.message}</span> : null}
        </label>
        <label className="block">
          <span className="text-sm font-medium text-ink">密码</span>
          <input
            className="mt-2 w-full rounded-md border border-ink/15 px-3 py-2 outline-none focus:border-pine focus:ring-2 focus:ring-mint"
            type="password"
            autoComplete="current-password"
            {...register("password")}
          />
          {errors.password ? <span className="mt-1 block text-xs text-coral">{errors.password.message}</span> : null}
        </label>
        {errorMessage ? (
          <div className="rounded-md border border-coral/30 bg-coral/10 px-3 py-2 text-sm text-coral">
            {errorMessage}
          </div>
        ) : null}
        <button
          className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-pine px-4 py-2.5 font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
          disabled={isSubmitting}
          type="submit"
        >
          <LogIn size={18} />
          {isSubmitting ? "登录中..." : "登录"}
        </button>
      </form>
    </section>
  );
}

function getRoleHomePath(role: UserRole): string {
  if (role === USER_ROLES.member) {
    return "/family/home";
  }

  if (role === USER_ROLES.orgAdmin) {
    return "/institution/store/applications";
  }

  if (PLATFORM_REVIEW_ROLES.includes(role)) {
    return "/platform/home";
  }

  return "/403";
}
