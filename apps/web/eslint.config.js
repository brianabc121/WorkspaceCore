import js from '@eslint/js'
import ts from 'typescript-eslint'
import vue from 'eslint-plugin-vue'
import globals from 'globals'

export default ts.config(
  { ignores: ['dist/**', 'node_modules/**', 'src/services/schema.d.ts'] },
  js.configs.recommended,
  ...ts.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    files: ['**/*.{ts,vue,js}'],
    languageOptions: { globals: globals.browser, parserOptions: { parser: ts.parser } },
    rules: { 'vue/multi-word-component-names': 'off', 'vue/max-attributes-per-line': 'off', 'vue/singleline-html-element-content-newline': 'off', 'vue/html-self-closing': 'off' },
  },
  { files: ['*.config.{js,ts}', 'tests/**/*.{ts,cjs}'], languageOptions: { globals: globals.node } },
  { files: ['tests/**/*.cjs'], languageOptions: { globals: globals.browser }, rules: { '@typescript-eslint/no-require-imports': 'off' } },
)
