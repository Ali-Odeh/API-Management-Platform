import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { developerGoTrue, memberGoTrue, resetState, server } from './server'

// Whole-app renders on a busy machine (tests run in parallel) can take a while.
configure({ asyncUtilTimeout: 5000 })

// jsdom lacks these browser APIs; Radix UI and the theme use them.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }),
})

class ResizeObserverStub {
  observe() {
    return undefined
  }
  unobserve() {
    return undefined
  }
  disconnect() {
    return undefined
  }
}
globalThis.ResizeObserver = ResizeObserverStub

Element.prototype.scrollIntoView = function scrollIntoView() {
  return undefined
}
Element.prototype.hasPointerCapture = () => false
Element.prototype.releasePointerCapture = () => undefined

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' })
})

afterEach(() => {
  cleanup()
  server.resetHandlers()
  localStorage.clear()
  sessionStorage.clear()
  memberGoTrue.users.clear()
  memberGoTrue.calls.length = 0
  developerGoTrue.users.clear()
  developerGoTrue.calls.length = 0
  resetState()
})

afterAll(() => {
  server.close()
})
