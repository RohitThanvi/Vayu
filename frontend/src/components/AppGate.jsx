/**
 * AppGate.jsx
 *
 * Gate order, top to bottom: Clerk sign-in first, THEN the tier picker.
 * <SignedOut> renders LandingPage (marketing page + Sign In/Sign Up,
 * via Clerk) and nothing else reachable — no tier picker, no App, no
 * data. <SignedIn> is required before the tier picker (and therefore
 * App.jsx, and therefore every feature) becomes reachable at all. This
 * is a UI-level gate for a smooth experience only; the real enforcement
 * is server-side (backend/app/main.py's dependencies=[Depends(get_current_user)]
 * on every data router) — even if someone bypassed this component
 * entirely, the API would still reject every call without a valid
 * Clerk session token.
 */

import { useState } from 'react';
import { SignedIn, SignedOut, ClerkLoading, ClerkLoaded } from '@clerk/clerk-react';
import App from '../App.jsx';
import LandingPage from './LandingPage.jsx';
import { API_URL } from '../lib/api.js';

const TIER_KEY = 'vayu_service_tier';

function LoadingScreen() {
  return (
    <div style={{ position: 'fixed', inset: 0, background: '#05070c', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ width: 26, height: 26, border: '3px solid #2a3040', borderTopColor: '#c9a86a', borderRadius: '50%', animation: 'vayu-gate-spin 0.8s linear infinite' }} />
      <style>{'@keyframes vayu-gate-spin { to { transform: rotate(360deg); } }'}</style>
    </div>
  );
}

export default function AppGate() {
  const [tier, setTier] = useState(() => localStorage.getItem(TIER_KEY));

  const handleSelectTier = (selected) => {
    localStorage.setItem(TIER_KEY, selected);
    setTier(selected);
  };

  const handleChangeTier = () => {
    localStorage.removeItem(TIER_KEY);
    setTier(null);
  };

  return (
    <>
      <ClerkLoading>
        <LoadingScreen />
      </ClerkLoading>
      <ClerkLoaded>
        <SignedOut>
          <LandingPage apiUrl={API_URL} />
        </SignedOut>
        <SignedIn>
          {!tier ? (
            <LandingPage apiUrl={API_URL} onSelectTier={handleSelectTier} skipToTierPicker />
          ) : (
            <App tier={tier} onChangeTier={handleChangeTier} />
          )}
        </SignedIn>
      </ClerkLoaded>
    </>
  );
}
