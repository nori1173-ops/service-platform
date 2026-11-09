import { describe, expect, it } from 'vitest'
import { toJstInput, jstToIso, intervalError, isEnded, isGrantActive, isGrantEnded, serviceName } from '../periodLabels'
import { formatJst } from '../labels'
import { period } from './periodFixtures'

describe('利用期間の日時', () => {
  it('実行環境のタイムゾーンによらずJSTで入出力する', () => {
    expect(toJstInput('2026-11-04T01:23:45Z')).toBe('2026-11-04T10:23:45')
    expect(jstToIso('2026-11-04T10:23')).toBe('2026-11-04T10:23:00+09:00')
    expect(jstToIso('2026-11-04T10:23:45')).toBe('2026-11-04T10:23:45+09:00')
    // 入力欄の値の正規化で 0 のミリ秒が付くことがある (jsdom)。秒の精度のまま受ける
    expect(jstToIso('2026-11-04T10:23:45.000')).toBe('2026-11-04T10:23:45+09:00')
    expect(formatJst('2026-11-04T01:23:45Z')).toContain('10:23')
  })
  it.each(['', '2026-02-30T10:00', '2026-11-04T25:00', '2026-11-04', '2026-11-04T10:00:00Z', '2026-11-04T10:00:00.001'])('不正な日時 %s', (value) => {
    expect(jstToIso(value)).toBeNull()
  })
  it('開始は利用期間以後、終了は開始より後で、省略できる', () => {
    const at = '2026-11-04T01:00:30Z'
    expect(intervalError('2026-11-04T10:00:29', '', at)).toBeTruthy()
    expect(intervalError('2026-11-04T10:00:30', '', at)).toBeNull()
    expect(intervalError('2026-11-04T10:00:30', '2026-11-04T10:00:30', at)).toBeTruthy()
    expect(intervalError('2026-11-04T10:00:30', 'bad', at)).toBeTruthy()
  })
  it('期限ちょうどで終了扱いとする', () => {
    const now = Date.parse('2026-11-04T01:00:00Z')
    expect(isEnded(null, now)).toBe(false)
    expect(isEnded('2026-11-04T01:00:00Z', now)).toBe(true)
    expect(isEnded('2026-11-04T01:00:01Z', now)).toBe(false)
    expect(serviceName('camera')).toBe('カメラ画像判定')
  })
  it('一覧の今有効な許可は 開始 <= 今 < 終了 と状態で決める (詳細の「終える」の判定とは別)', () => {
    const now = Date.parse('2026-11-04T01:00:00Z')
    const grant = period().grants[0]
    const before = { ...grant, starts_at: '2026-11-04T01:00:01Z' }
    expect(isGrantActive(before, now)).toBe(false)
    expect(isGrantActive({ ...grant, starts_at: '2026-11-04T01:00:00Z' }, now)).toBe(true)
    expect(isGrantActive({ ...grant, ends_at: '2026-11-04T01:00:00Z' }, now)).toBe(false)
    expect(isGrantActive({ ...grant, ends_at: '2026-11-04T01:00:01Z' }, now)).toBe(true)
    expect(isGrantActive({ ...grant, ends_at: null }, now)).toBe(true)
    expect(isGrantActive({ ...grant, status: 'ended' }, now)).toBe(false)
    // 開始前の許可は今は有効でないが、詳細の画面では終えられる
    expect(isGrantEnded(before, now)).toBe(false)
  })
})
