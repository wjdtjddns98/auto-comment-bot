import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Input";

export default function LoginPage() {
  const { user, login, isLoggingIn, loginError } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const location = useLocation();

  if (user) {
    const from = (location.state as { from?: { pathname: string } } | null)?.from?.pathname ?? "/";
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch {
      // 실패 사유는 loginError 로 화면에 표시됨
    }
  }

  return (
    <div className="mx-auto mt-20 max-w-sm">
      <h1 className="mb-4 text-xl font-semibold text-gray-900">로그인</h1>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <Field label="이메일" htmlFor="login-email">
          <Input
            id="login-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </Field>
        <Field label="비밀번호" htmlFor="login-password">
          <Input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </Field>
        {loginError && <p className="text-sm text-tone-danger">{loginError}</p>}
        <Button type="submit" disabled={isLoggingIn}>
          {isLoggingIn ? "로그인 중…" : "로그인"}
        </Button>
      </form>
    </div>
  );
}
