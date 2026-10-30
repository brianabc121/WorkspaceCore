import { fireEvent, render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'
import WidgetEditor from '../src/features/dashboard/WidgetEditor.vue'
import { widget } from './dashboard-fixtures'

describe('组件编辑器的默认布局', () => {
  it.each([
    [0, 0],
    [996, 996],
    [999, 999],
    [1002, 999],
    [1023, 999],
  ])('allows creation when the current bottom is %i (places at row %i)', async (bottom, expectedY) => {
    const view = render(WidgetEditor, { props: { bottom, busy: false, error: '' } })
    await fireEvent.click(screen.getByRole('button', { name: '保存组件' }))

    expect(view.emitted().save).toEqual([[{
      widget_type: 'note',
      config: { text: '' },
      layout: { desktop: { x: 0, y: expectedY, w: 6, h: 3 }, mobile: { y: expectedY, h: 3 } },
    }]])
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps the selected widget type and its size when limiting a new row', async () => {
    const view = render(WidgetEditor, { props: { bottom: 1002, busy: false, error: '' } })
    await fireEvent.update(screen.getByLabelText('组件类型'), 'today_tasks')
    await fireEvent.click(screen.getByRole('button', { name: '保存组件' }))

    expect(view.emitted().save).toEqual([[{
      widget_type: 'today_tasks',
      config: { limit: 5 },
      layout: { desktop: { x: 0, y: 999, w: 8, h: 4 }, mobile: { y: 999, h: 4 } },
    }]])
  })

  it('preserves an existing widget layout when changing its configuration', async () => {
    const existing = { ...widget, layout: { desktop: { x: 3, y: 7, w: 8, h: 4 }, mobile: { y: 12, h: 5 } } }
    const view = render(WidgetEditor, { props: { widget: existing, bottom: 1023, busy: false, error: '' } })
    await fireEvent.update(screen.getByLabelText('组件配置 JSON'), '{"text":"更新便签"}')
    await fireEvent.click(screen.getByRole('button', { name: '保存组件' }))

    expect(view.emitted().save).toEqual([[{ widget_type: 'note', config: { text: '更新便签' }, layout: existing.layout }]])
  })

  it('continues rejecting invalid configurations at the row boundary', async () => {
    const view = render(WidgetEditor, { props: { bottom: 1002, busy: false, error: '' } })
    await fireEvent.update(screen.getByLabelText('组件配置 JSON'), '{"text":42}')
    await fireEvent.click(screen.getByRole('button', { name: '保存组件' }))

    expect(view.emitted().save).toBeUndefined()
    expect(screen.getByRole('alert')).toHaveTextContent('组件配置或布局无效')
  })
})
