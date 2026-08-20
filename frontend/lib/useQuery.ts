"use client";

import { useEffect, useState } from "react";

/** Reads a query-string parameter after mount (static export friendly). */
export function useQueryParam(name: string): string | null {
  const [value, setValue] = useState<string | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setValue(params.get(name));
  }, [name]);
  return value;
}
