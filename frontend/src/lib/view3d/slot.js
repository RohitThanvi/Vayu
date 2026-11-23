// The slot for the 3D-view button: a Leaflet control in the SAME corner as the draw tools, added right after them, so it sits directly below the rectangle / polygon / edit / delete buttons.
// It starts hidden (the launcher shows it only at city zoom) and swallows clicks so pressing it never starts a draw or pans the map. Returns the DOM element the launcher renders the button into.
// L is passed in (Leaflet is a CDN global in this app) so this can be tested with a small stand-in.
export function createView3dSlot(L, map) {
  const Control = L.Control.extend({
    options: { position: 'topright' },
    onAdd() {
      const el = L.DomUtil.create('div', 'leaflet-bar leaflet-control vayu-3d-slot');
      el.style.display = 'none';
      L.DomEvent.disableClickPropagation(el);
      L.DomEvent.disableScrollPropagation(el);
      return el;
    },
  });
  const control = new Control();
  map.addControl(control);
  return control.getContainer();
}
