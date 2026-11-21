// Number formatting for the risk assessment panel: whole numbers keep their zeros (50 stays 50), decimals lose trailing zeros, missing or non-finite values read n/a.
export const fmt = (v, nd = 1) => (typeof v === 'number' && Number.isFinite(v) ? v.toFixed(nd).replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1') : 'n/a');
