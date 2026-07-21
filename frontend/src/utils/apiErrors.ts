export type Translate = (key: string) => string;

interface ApiErrorDetail {
  code?: string;
  message?: string;
}

export function apiErrorCode(error: unknown): string | null {
  if (!error || typeof error !== "object" || !("response" in error)) return null;
  const detail = (error as { response?: { data?: { detail?: unknown; code?: unknown } } })
    .response?.data;
  if (typeof detail?.code === "string" && detail.code.trim()) return detail.code.trim();
  if (detail?.detail && typeof detail.detail === "object") {
    const code = (detail.detail as ApiErrorDetail).code;
    if (typeof code === "string" && code.trim()) return code.trim();
  }
  return null;
}

export function localizedApiError(
  error: unknown,
  translate: Translate,
  fallbackKey: string,
): string {
  const code = apiErrorCode(error);
  if (code) {
    const key = `foundry.error_code.${code.toLowerCase()}`;
    const localized = translate(key);
    if (localized !== key) return localized;
  }

  if (error && typeof error === "object" && "response" in error) {
    const detail = (error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
      const message = (detail as ApiErrorDetail).message;
      if (typeof message === "string" && message.trim()) return message;
    }
  }

  if (error instanceof Error && error.message) return error.message;
  return translate(fallbackKey);
}
