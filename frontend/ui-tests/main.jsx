import { runAll } from './harness.jsx';
import { register as registerViews } from './views.test.jsx';
import { register as registerAlerts } from './alerts.test.jsx';
import { register as registerBar } from './bar.test.jsx';
export async function main() { registerViews(); registerAlerts(); registerBar(); return runAll(); }
