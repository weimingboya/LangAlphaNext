import { useState, type FormEvent } from "react";

import { BrandMark } from "../../shared/ui/BrandMark";

interface AuthGateProps {
  initialError?: string | null;
  onSignIn: (email: string, password: string) => Promise<void>;
}

export function AuthGate({ initialError, onSignIn }: AuthGateProps) {
  const [error, setError] = useState<string | null>(initialError || null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const form = new FormData(event.currentTarget);
    try {
      await onSignIn(String(form.get("email") || ""), String(form.get("password") || ""));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="auth-gate">
      <form className="auth-card" onSubmit={submit}>
        <BrandMark className="empty-brand-mark" />
        <h1>Sign in to LangAlpha</h1>
        <p>Your research threads and files stay private to your account.</p>
        <label>
          Email
          <input name="email" type="email" autoComplete="email" required />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        <button type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
        {error ? (
          <p className="auth-error" role="alert">
            {error}
          </p>
        ) : null}
      </form>
    </section>
  );
}
