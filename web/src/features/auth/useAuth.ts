import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { AuthSession, PublicConfig } from "../../domain/types";
import {
  ApiClient,
  fetchPublicConfig,
  persistSession,
  readStoredSession,
  requestSupabaseSession,
} from "../../shared/api/api-client";

export interface AuthState {
  client: ApiClient | null;
  config: PublicConfig | null;
  error: string | null;
  loading: boolean;
  session: AuthSession | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

export function useAuth(): AuthState {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [session, setSessionState] = useState<AuthSession | null>(() =>
    readStoredSession(),
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const sessionRef = useRef(session);

  const setSession = useCallback((next: AuthSession | null) => {
    const persisted = persistSession(next);
    sessionRef.current = persisted;
    setSessionState(persisted);
  }, []);

  useEffect(() => {
    let active = true;
    fetchPublicConfig()
      .then((value) => {
        if (active) setConfig(value);
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (session?.access_token) persistSession(session);
  }, [session]);

  const client = useMemo(
    () =>
      config
        ? new ApiClient(
            config,
            () => sessionRef.current,
            setSession,
          )
        : null,
    [config, setSession],
  );

  const signIn = useCallback(
    async (email: string, password: string) => {
      if (!config) throw new Error("Authentication is not ready");
      setError(null);
      const next = await requestSupabaseSession(config, "token?grant_type=password", {
        email,
        password,
      });
      setSession(next);
    },
    [config, setSession],
  );

  const signOut = useCallback(async () => {
    const current = sessionRef.current;
    try {
      if (config && current?.access_token) {
        await fetch(`${config.supabase_url}/auth/v1/logout?scope=local`, {
          method: "POST",
          headers: {
            apikey: config.supabase_publishable_key,
            Authorization: `Bearer ${current.access_token}`,
          },
        });
      }
    } finally {
      setSession(null);
    }
  }, [config, setSession]);

  return { client, config, error, loading, session, signIn, signOut };
}
