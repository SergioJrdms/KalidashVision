import { useEffect, useState } from "react";
import type { Session, User } from "@supabase/supabase-js";
import { supabase } from "../lib/supabase";

export interface AuthState {
  user: User | null;
  session: Session | null;
  loading: boolean;
  empresa: string;
}

export function useAuth(): AuthState {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => {
      setSession(s);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  const empresa = (session?.user?.user_metadata?.empresa as string) || "";
  return { user: session?.user ?? null, session, loading, empresa };
}
