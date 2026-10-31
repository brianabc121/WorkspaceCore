import type { TypedSchema, TypedSchemaError } from 'vee-validate'
import type { z } from 'zod'

// vee-validate 的 TypedSchema 适配 zod 4（@vee-validate/zod 只支持 zod 3，见 T-0044）。
// 只实现表单用到的 parse / cast：parse 把 zod 的错误按字段路径归组，cast 用于初始值。
type Issue = z.core.$ZodIssue

function fieldPath(path: readonly PropertyKey[]): string {
  return path.reduce<string>((out, part, i) => typeof part === 'number' ? `${out}[${part}]` : `${out}${i ? '.' : ''}${String(part)}`, '')
}

function collect(issues: readonly Issue[], prefix: readonly PropertyKey[], into: Map<string, TypedSchemaError>): void {
  for (const issue of issues) {
    const path = [...prefix, ...issue.path]
    // 联合类型（例如 token.or(z.literal('')))）失败时，用各分支的具体原因，而不是笼统的“输入无效”
    if (issue.code === 'invalid_union' && issue.errors.length) {
      collect(issue.errors.flat(), path, into)
      continue
    }
    const key = fieldPath(path)
    const entry = into.get(key) ?? { path: key, errors: [] }
    if (!entry.errors.includes(issue.message)) entry.errors.push(issue.message)
    into.set(key, entry)
  }
}

export function toTypedSchema<S extends z.ZodType>(schema: S): TypedSchema<z.input<S>, z.output<S>> {
  return {
    __type: 'VVTypedSchema',
    async parse(values) {
      const result = await schema.safeParseAsync(values)
      if (result.success) return { value: result.data, errors: [] }
      const errors = new Map<string, TypedSchemaError>()
      collect(result.error.issues, [], errors)
      return { errors: [...errors.values()] }
    },
    cast(values) {
      const result = schema.safeParse(values)
      return (result.success ? result.data : values) as z.input<S>
    },
  }
}
