export function extract422Message(err: unknown): string | null {
  const resp = (err as {
    response?: { status?: number; data?: { detail?: unknown } | unknown };
  })?.response;
  if (resp?.status !== 422) return null;

  const payload =
    typeof resp.data === 'object' && resp.data !== null && 'detail' in resp.data
      ? (resp.data as { detail?: unknown }).detail
      : resp.data;

  return normalize422Detail(payload);
}

function normalize422Detail(detail: unknown): string | null {
  if (typeof detail === 'string') return detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (!item || typeof item !== 'object') return '';
        const msg = (item as { detail?: string; msg?: string }).detail ??
          (item as { detail?: string; msg?: string }).msg ??
          '';
        const loc = Array.isArray((item as { loc?: Array<string | number> }).loc)
          ? ` (${(item as { loc?: Array<string | number> }).loc!.join('.')})`
          : '';
        return `${msg}${loc}`.trim();
      })
      .filter(Boolean)
      .join('; ') || null;
  }

  if (!detail || typeof detail !== 'object') return null;

  const obj = detail as {
    code?: string;
    message?: string;
    detail?: string;
    violations?: Array<{ rule_id?: string; message?: string }>;
    validation_errors?: Array<{ code?: string; detail?: string }>;
  };

  if (typeof obj.detail === 'string') return obj.detail;

  if (Array.isArray(obj.validation_errors) && obj.validation_errors.length > 0) {
    const parts = obj.validation_errors.map((item) => {
      const prefix = item.code ? `${item.code}: ` : '';
      return `${prefix}${item.detail ?? 'Validation error'}`;
    });
    if (typeof obj.message === 'string' && obj.message) {
      return `${obj.message}: ${parts.join('; ')}`;
    }
    return parts.join('; ');
  }

  if (Array.isArray(obj.violations) && obj.violations.length > 0) {
    const parts = obj.violations.map((item) => {
      const prefix = item.rule_id ? `${item.rule_id}: ` : '';
      return `${prefix}${item.message ?? 'Rule violation'}`;
    });
    if (typeof obj.message === 'string' && obj.message) {
      return obj.message;
    }
    return parts.join('; ');
  }

  if (typeof obj.message === 'string') return obj.message;
  if (typeof obj.code === 'string') return obj.code;
  return null;
}
