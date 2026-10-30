export function formatTime(value: string | null) {
  if (!value) return '暂无记录'
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? '时间不可用' : time.toLocaleString()
}
