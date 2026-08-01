import { AuthGate } from "./features/auth/AuthGate";
import { useAuth } from "./features/auth/useAuth";
import { ResearchWorkspace } from "./features/research/ResearchWorkspace";
import { BrandMark } from "./shared/ui/BrandMark";

export function App() {
  const auth = useAuth();

  if (auth.loading) {
    return (
      <section className="auth-gate">
        <div className="auth-card">
          <BrandMark className="empty-brand-mark loading-mark" />
          <h1>LangAlpha</h1>
          <p>Loading secure workspace…</p>
        </div>
      </section>
    );
  }
  if (!auth.session || !auth.client) {
    return <AuthGate initialError={auth.error} onSignIn={auth.signIn} />;
  }
  return <ResearchWorkspace client={auth.client} onSignOut={auth.signOut} />;
}
