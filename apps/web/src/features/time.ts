// 界面上统一的时间格式：中文、24 小时制、到分钟，用浏览器所在时区（T-0083）。
// 例：2026/11/02 14:05。精确到秒的时间放在 <time datetime> 属性里。
const formatter = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })

export function formatTime(value: string | null) {
  if (!value) return '暂无记录'
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? '时间不可用' : formatter.format(time)
}
