"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "@/lib/session";

/** Nothing lives at the root: signed in goes to the library, signed out to sign-in. */
export default function Index() {
  const { status } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (status === "signed-in") router.replace("/knowledge");
    if (status === "signed-out") router.replace("/sign-in");
  }, [status, router]);

  return null;
}
