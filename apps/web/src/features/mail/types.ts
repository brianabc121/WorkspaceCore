// 邮件页组件之间传的类型（T-0171）

// 阅读器顶部的“所属邮箱”；回复固定用它发出
export type Owner = { id: string; name: string; email: string; tone: string; canSend: boolean; reason: string | null }

// 写邮件 / 回复的草稿：senderId 为空表示还没选发信邮箱；reply 只属于原邮件所在的邮箱
export type ReplyTo = { accountId: string; messageId: string; subject: string }
export type Draft = { senderId: string; to: string; cc: string; bcc: string; subject: string; body: string; reply: ReplyTo | null }
