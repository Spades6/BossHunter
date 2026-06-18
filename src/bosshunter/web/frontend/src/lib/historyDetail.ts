import type { HistoryItem } from '@/hooks/useDashboard'

export interface ParsedHistoryDetail {
  schema: string
  hrQuestion: string
  aiReply: string
  conversationTail: Array<{ sender: string; text: string }>
}

export function parseHistoryDetail(item: HistoryItem): ParsedHistoryDetail {
  if (item.detail_payload) {
    return {
      schema: item.detail_payload.schema || 'unknown',
      hrQuestion: item.detail_payload.hr_question || '',
      aiReply: item.detail_payload.ai_reply || '',
      conversationTail: item.detail_payload.conversation_tail || [],
    }
  }

  if (!item.detail) {
    return {
      schema: 'legacy_text',
      hrQuestion: '',
      aiReply: '',
      conversationTail: [],
    }
  }

  try {
    const parsed = JSON.parse(item.detail)
    if (parsed && typeof parsed === 'object') {
      return {
        schema: parsed.schema || 'unknown',
        hrQuestion: parsed.hr_question || '',
        aiReply: parsed.ai_reply || '',
        conversationTail: Array.isArray(parsed.conversation_tail) ? parsed.conversation_tail : [],
      }
    }
  } catch {
    // Legacy text detail.
  }

  return {
    schema: 'legacy_text',
    hrQuestion: '',
    aiReply: item.detail,
    conversationTail: [],
  }
}
