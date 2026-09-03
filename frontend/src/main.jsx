import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import './index.css' // Import Tailwind styles

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)

// Hide the native splash screen once the app has mounted (no-op on web).
import('@capacitor/splash-screen')
  .then(({ SplashScreen }) => SplashScreen.hide())
  .catch(() => {});
