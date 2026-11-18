# 依赖扫描：npm 公告的临时例外，第一条是 picomatch（T-0290）

- 日期：2026-11-18
- 状态：accepted（人类 2026-11-18 在对话中同意“按照推荐的来”：只放过这一条公告、带到期日，上游修复后删掉）
- 相关：`scripts/security-scan.sh`、`scripts/security-exceptions.txt`、`.github/workflows/ci-security.yml`、
  `docs/decisions/2026-10-31-sprint-6-backup-and-scan-verification.md` 第 5.2 节、`docs/deployment.md` 第 15.1 节

## 1. 起因

2026-11-17 起，`ci-security` 的 `deps` job 在所有 PR 和 main 上失败：`apps/web` 的 `npm audit --audit-level=high`
报出 picomatch 的公告 [GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf)
（“picomatch vulnerable to ReDoS through single-branch repeated extglobs”，高危，影响 `<=4.0.7`）。
装的正是最新的 `picomatch@4.0.7`，npm 上还没有修复版本；`npm audit` 给的“修复”是把 vite、vitest、vue-tsc 降级到旧的大版本，不能用。

## 2. 影响核对（本机 2026-11-18 实测）

- 依赖路径（`npm ls picomatch --all`）：只经 `vite`（直接和经 `fdir`、`tinyglobby`）、`vitest`、`vue-tsc`（经 `@vue/language-core`）引入；
  `npm audit` 里另外几条高危（`vite`、`vitest`、`vue-tsc`、`fdir`、`tinyglobby`、`@vue/language-core`、`@typescript-eslint/typescript-estree`）
  都是“经由”这一条，没有自己的公告。
- 它们都在 `apps/web` 的 `devDependencies` 里；`npm ls picomatch --omit=dev --all` 为空。
- 生产的前端镜像（`apps/web/Dockerfile`）只把构建出的静态文件交给 nginx，镜像里没有 Node 和这些包；
  picomatch 只在开发者本机和 CI 里处理仓库自己的路径模式，不处理用户输入。
- `tests/e2e` 没有公告。

## 3. 决定

1. `scripts/security-scan.sh deps` 的 npm 部分改为按 `npm audit --json` 逐条判定（Python 部分不变）：
   - 高危及以上、又不在有效例外里的公告 → 失败；
   - `npm audit` 本身出错（连不上公告库等）→ 失败，不能当作“没有漏洞”；
   - 中低危列出来，只报告；
   - 例外到期后自动失效，照常失败，并写明“例外已到期”。
2. 例外登记在 `scripts/security-exceptions.txt`：一行一条“公告编号 最后有效日期（含当天，UTC） 说明”。
   只登记没有修复版本、并且确认不影响生产的公告；新增一条要有任务和人类同意。
3. 第一条：`GHSA-fc5q-39xc-vjpf`，到 **2026-12-18** 为止（30 天）。

## 4. 后续

- picomatch 发布修复版本（或 vite / vitest / vue-tsc 换掉它）后：升级依赖、删掉这一行，由新任务处理。
- 到期前上游仍未修复：由人类决定是否延期（改日期也要有任务记录），不能为了让 CI 变绿悄悄延期。
- 每周一的定时扫描仍会跑，到期当天之后的扫描会失败提醒。
