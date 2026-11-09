import { readFileSync, realpathSync, statSync } from 'node:fs'
import { resolve } from 'node:path'

const configuredSecretsDir = process.env.E2E_SECRETS_DIR?.trim()
if (!configuredSecretsDir) {
  throw new Error('E2E_SECRETS_DIR に create dev --secrets-dir と同じ秘密ディレクトリを指定してください')
}
const requestedSecretsDir = resolve(configuredSecretsDir)
function rejectWindowsMount(path: string) {
  if (path === '/mnt' || path.startsWith('/mnt/')) {
    throw new Error('E2E_SECRETS_DIR は /mnt/ の下に置けません。Linux 側の専用ディレクトリを指定してください')
  }
}
rejectWindowsMount(requestedSecretsDir)
export const secretsDir = realpathSync(requestedSecretsDir)
rejectWindowsMount(secretsDir)
if (!statSync(secretsDir).isDirectory() || (statSync(secretsDir).mode & 0o7777) !== 0o700) {
  throw new Error('秘密ディレクトリの権限は 700 にしてください (既存の権限は変更しません)')
}
export const manifestFile = resolve(secretsDir, 'e2e-users.json')
export const customerStateFile = resolve(secretsDir, 'customer-state.json')
export const baseURL = process.env.E2E_BASE_URL ?? 'https://service-platform-dev.example-cloud.com'
if (new URL(baseURL).origin !== 'https://service-platform-dev.example-cloud.com') {
  throw new Error('E2E は dev の URL だけを使用できます')
}

export interface E2EUser {
  email: string
  password: string
  username: string
  user_id: string
  totp_secret?: string
  totp_last_step?: number
}
export interface Manifest {
  env: string
  ready: boolean
  deleted: boolean
  client_id: string
  run_id: string
  users: Record<'customer' | 'target' | 'admin', E2EUser>
}

export function manifest(): Manifest {
  const path = resolve(secretsDir, 'e2e-users.json')
  if ((statSync(path).mode & 0o7777) !== 0o600) throw new Error('秘密ファイルを 600 にしてください')
  const state = JSON.parse(readFileSync(path, 'utf8')) as Manifest
  if (state.env !== 'dev' || !state.ready || state.deleted) throw new Error('dev E2E 利用者を作成してください')
  return state
}
