import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { RiskDot } from '@/pages/settings/security/components/RiskDot'

describe('RiskDot', () => {
  it.each([
    ['low', 'bg-risk-low'],
    ['medium', 'bg-risk-medium'],
    ['high', 'bg-risk-high'],
  ] as const)('applies semantic %s color token', (level, expectedClass) => {
    render(<RiskDot level={level} />)
    const dot = screen.getByRole('img')
    expect(dot.className).toContain(expectedClass)
  })

  it('renders an aria label and title for the locked critical level', () => {
    render(<RiskDot level="critical" />)
    const dot = screen.getByRole('img')
    expect(dot.getAttribute('aria-label')).toBe('Always On')
    expect(dot.getAttribute('title')).toBe('Always On')
  })

  it('forwards extra className from the caller', () => {
    render(<RiskDot level="low" className="mt-2" />)
    const dot = screen.getByRole('img')
    expect(dot.className).toContain('mt-2')
  })
})
