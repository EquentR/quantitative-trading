import { describe, expect, test } from 'vitest'
import { loadConfigFromFile } from 'vite'

describe('vite dev server config', () => {
  test('proxies API requests to the local backend', async () => {
    const loaded = await loadConfigFromFile(
      { command: 'serve', mode: 'development' },
      'vite.config.ts',
    )
    if (!loaded) {
      throw new Error('vite config was not loaded')
    }
    const config = loaded.config

    expect(config.server?.proxy?.['/api']).toMatchObject({
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
    })
  })
})
