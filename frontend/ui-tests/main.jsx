import { runAll } from './harness.jsx';
import { register as registerViews } from './views.test.jsx';
import { register as registerAlerts } from './alerts.test.jsx';
import { register as registerBar } from './bar.test.jsx';
import { register as registerConnection } from './connection.test.jsx';
import { register as registerTierLanding } from './tierlanding.test.jsx';
import { register as registerSeo } from './seo.test.jsx';
import { register as registerIrrigation } from './irrigation.test.jsx';
import { register as registerPathway } from './pathway.test.jsx';
import { register as registerAssessment } from './assessment.test.jsx';
import { register as registerView3d } from './view3d.test.jsx';
export async function main() { registerViews(); registerAlerts(); registerBar(); registerConnection(); registerTierLanding(); registerSeo(); registerIrrigation(); registerPathway(); registerAssessment(); registerView3d(); return runAll(); }
