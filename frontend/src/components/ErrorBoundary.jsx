/**
 * ErrorBoundary.jsx
 * Class component (error boundaries can't be function components — this
 * is the one place React still requires a class) that catches any
 * uncaught error thrown during rendering anywhere in its subtree and
 * shows a recoverable inline message instead of letting the whole React
 * tree unmount, which is what "the screen goes blank" actually is: an
 * uncaught render exception with nothing catching it.
 *
 * This doesn't fix the underlying bug that threw — it contains the
 * blast radius so one bad field in one result doesn't take down the
 * entire app, and gives the user a way back (reload) instead of a dead
 * white screen with no information.
 */

import { Component } from 'react';

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error('VAYU render error caught by ErrorBoundary:', error, info?.componentStack);
  }

  render() {
    if (this.state.hasError) {
      // Optional lighter fallback for non-critical subtrees (e.g. the
      // commodity ticker) — render that instead of the full-screen
      // reload prompt, consistent with those components' own existing
      // "just stay invisible if something's wrong" design rather than
      // taking the whole app down over an optional extra layer.
      if (this.props.fallback !== undefined) return this.props.fallback;

      return (
        <div style={{
          position:'fixed', inset:0, display:'flex', alignItems:'center', justifyContent:'center',
          background:'#0a0c0f', color:'#e8e8e8', fontFamily:"'JetBrains Mono','SF Mono',Consolas,monospace",
          padding:20, textAlign:'center', zIndex:9999,
        }}>
          <div style={{ maxWidth:420 }}>
            <div style={{ fontSize:16, marginBottom:10, color:'#e8746b' }}>Something went wrong rendering this view.</div>
            <div style={{ fontSize:12, color:'#9a9fa8', marginBottom:20, lineHeight:1.5 }}>
              {this.state.error?.message || 'An unexpected error occurred.'}
            </div>
            <button onClick={() => window.location.reload()}
              style={{ padding:'10px 20px', background:'#1a1d22', border:'1px solid #3a4250', color:'#e8e8e8',
                fontFamily:'inherit', fontSize:13, cursor:'pointer', borderRadius:3 }}>
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
