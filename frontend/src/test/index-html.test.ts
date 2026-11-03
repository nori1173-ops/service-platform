import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { SITE_TITLE } from '@/config/site'

function read(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf-8')
}

describe('index.html', () => {
  const html = read('../../index.html')

  it('サイト表題 SERVICE-PLATFORM と日本語の説明を持つ', () => {
    expect(html).toContain('<html lang="ja">')
    expect(html).toMatch(/<title>[^<]*SERVICE-PLATFORM[^<]*<\/title>/)
    expect(html).toMatch(/<meta name="description" content="[^"]+"/)
  })

  // 起動前の静的タイトルと router が付ける document.title のベース名を揃える
  it('タイトルのベース名が site.ts の SITE_TITLE と一致する', () => {
    expect(html).toContain(`<title>${SITE_TITLE}</title>`)
  })

  it('SVG の favicon を参照し、その実体がある', () => {
    expect(html).toContain('rel="icon"')
    expect(html).toContain('/favicon.svg')
    expect(read('../../public/favicon.svg')).toContain('<svg')
  })
})
