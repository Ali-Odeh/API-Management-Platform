import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from '@/lib/theme'
import './index.css'

const root = document.getElementById('root')
if (!root) throw new Error('index.html has no #root element')

createRoot(root).render(
  <StrictMode>
    <ThemeProvider>
      <p className="p-8">API Platform</p>
    </ThemeProvider>
  </StrictMode>,
)
