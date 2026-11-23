import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ThemeProvider, useTheme } from './theme'

function Probe() {
  const { theme, resolved, setTheme } = useTheme()
  return (
    <>
      <p>
        {theme}/{resolved}
      </p>
      <button
        onClick={() => {
          setTheme('dark')
        }}
      >
        dark
      </button>
    </>
  )
}

describe('theme', () => {
  it('follows the system until a theme is chosen, and remembers the choice', () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )
    expect(screen.getByText('system/light')).toBeInTheDocument()
    act(() => {
      screen.getByRole('button').click()
    })
    expect(screen.getByText('dark/dark')).toBeInTheDocument()
    expect(document.documentElement).toHaveClass('dark')
    expect(localStorage.getItem('theme')).toBe('dark')
  })

  it('starts from the stored choice', () => {
    localStorage.setItem('theme', 'light')
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )
    expect(screen.getByText('light/light')).toBeInTheDocument()
  })

  it('follows the system setting as it changes', () => {
    let listener: (() => void) | undefined
    const query = {
      matches: false,
      addEventListener: (_: string, fn: () => void) => {
        listener = fn
      },
      removeEventListener: vi.fn(),
    }
    vi.spyOn(window, 'matchMedia').mockReturnValue(query as unknown as MediaQueryList)
    const { unmount } = render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )
    expect(screen.getByText('system/light')).toBeInTheDocument()
    query.matches = true
    act(() => listener?.())
    expect(screen.getByText('system/dark')).toBeInTheDocument()
    unmount()
    expect(query.removeEventListener).toHaveBeenCalled()
  })

  it('works when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    )
    expect(screen.getByText('system/light')).toBeInTheDocument()
    act(() => {
      screen.getByRole('button').click()
    })
    expect(screen.getByText('dark/dark')).toBeInTheDocument()
  })

  it('needs its provider', () => {
    expect(() => render(<Probe />)).toThrow('useTheme outside ThemeProvider')
  })
})
