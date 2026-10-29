import React from 'react'
import ReactDOM from 'react-dom/client'
import AppGate from './components/AppGate.jsx'
import ConnectionMonitor from './components/ConnectionMonitor.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import './index.css' // Import Tailwind styles
import { registerOfflineSupport } from './lib/registerOffline.js'

const API_URL = import.meta.env.VITE_API_URL !== undefined
  ? import.meta.env.VITE_API_URL
  : 'http://127.0.0.1:8000'   // same resolution as AppGate/App

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <AppGate />
      <ConnectionMonitor apiUrl={API_URL} />
    </ErrorBoundary>
  </React.StrictMode>,
)

// Hide the native splash screen once the app has mounted (no-op on web).
import('@capacitor/splash-screen')
  .then(({ SplashScreen }) => SplashScreen.hide())
  .catch(() => {});

// Offline fallback page (see public/sw.js). Production web builds only.
registerOfflineSupport({ isProd: import.meta.env.PROD });
