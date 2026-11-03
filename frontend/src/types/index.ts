export interface ApiError {
  code: string
  message: string
}

/** API 共通エンベロープ。 */
export interface ApiEnvelope<T> {
  ok: boolean
  data: T | null
  error: ApiError | null
  meta: Record<string, unknown> | null
}
