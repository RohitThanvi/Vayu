import React from 'react'
import ReactDOM from 'react-dom/client'
import { ClerkProvider } from '@clerk/clerk-react'
import AppGate from './components/AppGate.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import './index.css' // Import Tailwind styles

// From the Clerk Dashboard (Configure -> API Keys -> Publishable key).
// Safe to expose client-side (that's what "publishable" means) — the
// actual secret verification happens server-side in the backend against
// CLERK_JWT_KEY, never here.
const CLERK_PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY

if (!CLERK_PUBLISHABLE_KEY) {
  // Fail loudly in dev rather than silently rendering a broken app with
  // every feature 401ing — matches the backend's "fail closed" stance
  // in tier_gate.py when CLERK_JWT_KEY is missing.
  throw new Error(
    'Missing VITE_CLERK_PUBLISHABLE_KEY — set it in frontend/.env (and in ' +
    'Vercel/Render env vars for deployed builds). Get it from the Clerk ' +
    'Dashboard: Configure -> API Keys -> Publishable key.'
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY}>
      <ErrorBoundary>
        <AppGate />
      </ErrorBoundary>
    </ClerkProvider>
  </React.StrictMode>,
)

// Hide the native splash screen once the app has mounted (no-op on web).
import('@capacitor/splash-screen')
  .then(({ SplashScreen }) => SplashScreen.hide())
  .catch(() => {});
