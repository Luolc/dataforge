// Run before the bundle, so that the first paint is already in the colour
// template kept in localStorage; src/ui/template.ts names the key and checks
// the value once the app loads. A value no template has matches no block of
// theme.css, so the page keeps the default.
try {
  const chosen = localStorage.getItem("viz.template");
  if (chosen !== null) document.documentElement.dataset.template = chosen;
} catch {
  // No localStorage: the default template.
}
