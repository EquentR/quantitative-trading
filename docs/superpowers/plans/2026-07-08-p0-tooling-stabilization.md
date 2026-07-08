# P0 Tooling Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the current workspace, front-end Node runtime, front-end dependency versions, and verification path before implementing new trading features.

**Architecture:** Keep this phase limited to tooling, documentation, and current change validation. The Python backend remains unchanged except for running the existing test suite. The Web project uses Node 24 through nvm, exact package versions in `src/web/package.json`, and the existing `pnpm-lock.yaml`.

**Tech Stack:** Python pytest, Vue 3, Vite, Vitest, pnpm, nvm, Node 24, Git.

---

## Scope

Implement the remaining P0 tooling work from `docs/superpowers/specs/2026-07-08-next-roadmap-design.md`. The quote adapter fallback and cost-price precision cleanup are already part of the current baseline; this plan verifies that baseline through the Python test suite and focuses new edits on Node 24, exact Web dependency versions, and repeatable local commands.

This plan must not add trading recommendations, strategy logic, risk logic, scheduler changes, API routes, or Web decision pages.

## File Map

- Create: `.nvmrc`
  Pins local Node selection to Node 24 for `nvm use`.
- Modify: `src/web/package.json`
  Adds `engines.node` and replaces all `latest`/range direct dependency declarations with exact versions.
- Modify: `src/web/pnpm-lock.yaml`
  Regenerated or confirmed after package metadata changes.
- Create: `src/web/tests/package-config.test.ts`
  Guards Node engine and exact direct dependency versions.
- Modify: `README.md`
  Documents Node 24, nvm, pnpm install, Web test, and Web dev commands.
- No backend code changes.

## Exact Front-End Version Set

Use these exact direct dependency versions:

```json
{
  "@tanstack/vue-query": "5.101.2",
  "@vee-validate/zod": "4.9.4",
  "class-variance-authority": "0.7.1",
  "clsx": "2.1.1",
  "lucide-vue-next": "1.0.0",
  "pinia": "3.0.4",
  "radix-vue": "1.9.17",
  "tailwind-merge": "3.6.0",
  "vee-validate": "4.9.4",
  "vue": "3.5.39",
  "vue-router": "5.1.0",
  "zod": "4.4.3"
}
```

Use these exact dev dependency versions:

```json
{
  "@playwright/test": "1.61.1",
  "@testing-library/jest-dom": "6.9.1",
  "@testing-library/user-event": "14.6.1",
  "@testing-library/vue": "8.1.0",
  "@types/node": "26.1.0",
  "@vitejs/plugin-vue": "6.0.7",
  "@vue/test-utils": "2.4.11",
  "autoprefixer": "10.5.2",
  "jsdom": "29.1.1",
  "msw": "2.14.6",
  "postcss": "8.5.16",
  "tailwindcss": "3.4.19",
  "typescript": "5.7.2",
  "vite": "8.1.3",
  "vitest": "4.1.10",
  "vue-tsc": "3.3.6"
}
```

### Task 1: Add Package Configuration Guard

**Files:**
- Create: `src/web/tests/package-config.test.ts`
- Test: `src/web/tests/package-config.test.ts`

- [ ] **Step 1: Write the failing package configuration test**

Create `src/web/tests/package-config.test.ts`:

```ts
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
})
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
source ~/.nvm/nvm.sh
nvm use 24
pnpm -C src/web test -- package-config.test.ts
```

Expected: FAIL because `src/web/package.json` does not yet define `engines.node` and still contains non-exact versions such as `latest` or `^3.4.17`.

### Task 2: Pin Node 24 and Exact Web Dependency Versions

**Files:**
- Create: `.nvmrc`
- Modify: `src/web/package.json`
- Test: `src/web/tests/package-config.test.ts`

- [ ] **Step 1: Add `.nvmrc`**

Create `.nvmrc`:

```text
24
```

- [ ] **Step 2: Update `src/web/package.json`**

Replace the dependencies and devDependencies sections and add `engines` so `src/web/package.json` contains:

```json
{
  "name": "quantitative-trading-console",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "packageManager": "pnpm@10.30.3",
  "engines": {
    "node": ">=24 <25"
  },
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "vue-tsc --noEmit && vite build",
    "preview": "vite preview --host 127.0.0.1",
    "test": "vitest run",
    "test:watch": "vitest",
    "e2e": "playwright test"
  },
  "dependencies": {
    "@tanstack/vue-query": "5.101.2",
    "@vee-validate/zod": "4.9.4",
    "class-variance-authority": "0.7.1",
    "clsx": "2.1.1",
    "lucide-vue-next": "1.0.0",
    "pinia": "3.0.4",
    "radix-vue": "1.9.17",
    "tailwind-merge": "3.6.0",
    "vee-validate": "4.9.4",
    "vue": "3.5.39",
    "vue-router": "5.1.0",
    "zod": "4.4.3"
  },
  "devDependencies": {
    "@playwright/test": "1.61.1",
    "@testing-library/jest-dom": "6.9.1",
    "@testing-library/user-event": "14.6.1",
    "@testing-library/vue": "8.1.0",
    "@types/node": "26.1.0",
    "@vitejs/plugin-vue": "6.0.7",
    "@vue/test-utils": "2.4.11",
    "autoprefixer": "10.5.2",
    "jsdom": "29.1.1",
    "msw": "2.14.6",
    "postcss": "8.5.16",
    "tailwindcss": "3.4.19",
    "typescript": "5.7.2",
    "vite": "8.1.3",
    "vitest": "4.1.10",
    "vue-tsc": "3.3.6"
  }
}
```

- [ ] **Step 3: Update or confirm the pnpm lockfile**

Run:

```bash
source ~/.nvm/nvm.sh
nvm use 24
pnpm -C src/web install --lockfile-only
```

Expected: command exits 0. `src/web/pnpm-lock.yaml` is either unchanged or updated only for metadata consistent with the exact versions.

- [ ] **Step 4: Run the package configuration test**

Run:

```bash
source ~/.nvm/nvm.sh
nvm use 24
pnpm -C src/web test -- package-config.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add .nvmrc src/web/package.json src/web/pnpm-lock.yaml src/web/tests/package-config.test.ts
git commit -m "chore: pin web node and dependencies"
```

### Task 3: Document Web Runtime Commands

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add Web development section to README**

Add this section after the existing HTTP API service startup instructions:

````markdown
## 本地前端控制台开发

前端控制台使用 Node 24 和 pnpm。推荐通过 nvm 切换 Node 版本：

```bash
nvm use
pnpm -C src/web install
pnpm -C src/web test
pnpm -C src/web dev
```

如果本机尚未安装 Node 24：

```bash
nvm install 24
nvm use
```

前端开发服务会通过 Vite 将 `/api` 请求代理到本地后端 `http://127.0.0.1:8000`。先启动后端：

```bash
qt service run
```

前端只维护本地手动台账、资金账户、账户快照和调度状态，不自动真实下单，不控制真实交易客户端，不保存真实券商凭据。
````

- [ ] **Step 2: Run README docs sanity test**

Run:

```bash
.venv/bin/python -m pytest tests/test_docs_examples.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

Run:

```bash
git add README.md
git commit -m "docs: document web node runtime"
```

### Task 4: Run Full P0 Verification

**Files:**
- No source edits.

- [ ] **Step 1: Run Python tests**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS. A Starlette/httpx deprecation warning is acceptable if no tests fail.

- [ ] **Step 2: Run Web tests on Node 24**

Run:

```bash
source ~/.nvm/nvm.sh
nvm use 24
node -v
pnpm -C src/web test
```

Expected: `node -v` prints `v24.x.x`; Vitest exits 0.

- [ ] **Step 3: Run Web build on Node 24**

Run:

```bash
source ~/.nvm/nvm.sh
nvm use 24
pnpm -C src/web build
```

Expected: `vue-tsc --noEmit && vite build` exits 0.

- [ ] **Step 4: Check git diff for whitespace errors**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 5: Commit verification-only docs if needed**

If no files changed during verification, do not commit. If generated metadata changed unexpectedly, inspect it before committing. Do not commit `src/web/dist`, local data files, credentials, `.env`, or API keys.
