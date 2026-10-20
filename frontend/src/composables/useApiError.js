export const detailOf = (e, fallback) => {
  const d = e?.response?.data?.detail
  if (!d) return fallback
  return Array.isArray(d) ? d.map((x) => x.msg).join('; ') : String(d)
}

/**
 * Splits an API error detail (pydantic array or "; "-joined service string) into
 * pieces and files each under the form key `resolve(piece)` returns; pieces that
 * resolve to null stay only in the top-level alert.
 */
export const fieldErrorsOf = (e, resolve) => {
  const d = e?.response?.data?.detail
  let pieces = []
  if (Array.isArray(d)) pieces = d.map((x) => `${(x.loc || []).slice(1).join('.')}: ${x.msg}`)
  else if (typeof d === 'string') pieces = d.split('; ').map((s) => s.trim())
  const out = {}
  for (const piece of pieces) {
    const k = resolve(piece)
    if (k) out[k] = out[k] ? `${out[k]}; ${piece}` : piece
  }
  return out
}
