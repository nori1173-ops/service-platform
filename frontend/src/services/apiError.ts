import type { ApiError } from '@/types'

/** axios エラーから API エンベロープの `error` を取り出す。無ければ null。 */
export function extractApiError(error: unknown): ApiError | null {
  if (!error || typeof error !== 'object' || !('response' in error)) {
    return null
  }
  const data = (error as { response?: { data?: unknown } }).response?.data
  if (!data || typeof data !== 'object') {
    return null
  }
  const envelopeError = (data as { error?: unknown }).error
  if (
    envelopeError &&
    typeof envelopeError === 'object' &&
    typeof (envelopeError as ApiError).code === 'string' &&
    typeof (envelopeError as ApiError).message === 'string'
  ) {
    return envelopeError as ApiError
  }
  return null
}
