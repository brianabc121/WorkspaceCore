export function safeRedirect(value: unknown) {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') && !/^\/(login|setup|connection-error)([/?#]|$)/.test(value) ? value : '/'
}
