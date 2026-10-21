// Run before the bundle, so that the first paint is already in the colour
// template kept in localStorage, or in the default when there is none or it
// is none of the templates; src/ui/template.ts names the key, the templates
// and the default, and template.test.tsx checks that the two agree.
const templates = ["ac", "dark", "contrast", "neutral"];
let chosen = "ac";
try {
  const saved = localStorage.getItem("viz.template");
  if (templates.includes(saved)) chosen = saved;
} catch {
  // No localStorage: the default template.
}
document.documentElement.dataset.template = chosen;
