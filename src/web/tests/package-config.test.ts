import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, test } from 'vitest'

interface PackageJson {
  engines?: Record<string, string>
  dependencies?: Record<string, string>
  devDependencies?: Record<string, string>
}

const packageJson = JSON.parse(
  readFileSync(resolve(__dirname, '../package.json'), 'utf-8'),
) as PackageJson

const exactVersionPattern = /^\d+\.\d+\.\d+$/

describe('web package configuration', () => {
  test('requires Node 24 through engines', () => {
    expect(packageJson.engines?.node).toBe('>=24 <25')
  })

  test('does not use latest or range versions for direct dependencies', () => {
    const allDependencies = {
      ...packageJson.dependencies,
      ...packageJson.devDependencies,
    }

    expect(Object.keys(allDependencies).length).toBeGreaterThan(0)
    for (const [name, version] of Object.entries(allDependencies)) {
      expect(version, `${name} should use an exact version`).toMatch(exactVersionPattern)
    }
  })

  test('uses Apache ECharts for market visualizations', () => {
    expect(packageJson.dependencies?.echarts).toMatch(exactVersionPattern)
  })
})
