import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    rules: {
      // Q3: `_` 前缀的变量 / 参数 / catch 错误 都忽略 (常用的"故意未
      // 使用"惯例, 覆盖 _i / _fluxErr / _result / _setPageError 4 处).
      '@typescript-eslint/no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
      // Q3: 允许 constants 在 component 文件里共存, HMR 牺牲可接受 —
      // 拆 40 行 helper 到独立文件成本 > 收益.
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
])
